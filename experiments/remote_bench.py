"""Compare direct Docker, direct SSH and ∅ne on the same remote tasks."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import tempfile
import time

from remote_cases import CASES, PREVIOUS_STDOUT, install, verify
from remote_targets import IMAGE, RemoteTargets

ROOT = Path(__file__).resolve().parents[1]
MODES = ("ssh", "docker", "one")
MAX_ROUNDS = 3
MAX_ACTIONS = 3
MAX_OUTPUT_BYTES = 4096
MAX_CODE_BYTES = 12288
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "boolean"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "enum": ["east", "west"]},
                    "runtime": {"type": "string", "enum": ["bash", "python"]},
                    "code": {"type": "string"},
                },
                "required": ["target", "runtime", "code"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["done", "actions"],
    "additionalProperties": False,
}
MODE_HELP = {
    "ssh": "Direct SSH runs each action in a fresh interpreter. Files persist per target.",
    "docker": "Docker exec runs each action in a fresh interpreter. Files persist per target.",
    "one": "∅ne MCP and SSH retain a separate session for each target and runtime. Files, imports, variables and functions can persist between actions on the same target and runtime.",
}


def model_action(controller, schema, model, prompt, *, memory_file=None):
    args = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--skip-git-repo-check", "--json", "--color", "never", "-s", "read-only",
        "-C", str(controller), "-m", model, "-c", 'model_reasoning_effort="low"',
        "--output-schema", str(schema), "--", prompt,
    ]
    if memory_file is not None:
        args = ["/usr/bin/time", "-q", "-f", "%M", "-o", str(memory_file), *args]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return None, {}, "provider_timeout"
    usage = {}
    try:
        events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
        usage = next((e["usage"] for e in events if e["type"] == "turn.completed"), {})
        items = [e["item"] for e in events if e["type"] == "item.completed"]
        final = [i["text"] for i in items if i["type"] == "agent_message"]
        if proc.returncode or not usage or not final:
            return None, usage, "provider_or_controller_failure"
        if any(i["type"] in {"command_execution", "file_change", "mcp_tool_call", "web_search"} for i in items):
            return None, usage, "unexpected_controller_tool"
        decision = json.loads(final[-1])
        actions = decision["actions"]
        if not isinstance(decision["done"], bool) or not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
            raise ValueError("invalid plan")
        if not actions and not decision["done"]:
            raise ValueError("no progress")
        for action in actions:
            if (action["target"] not in ("east", "west") or action["runtime"] not in ("bash", "python")
                    or not 0 < len(action["code"].encode()) <= MAX_CODE_BYTES):
                raise ValueError("invalid action")
        return decision, usage, None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None, usage, "invalid_model_response"


def show(text):
    data = text.encode()
    if len(data) <= MAX_OUTPUT_BYTES:
        return text
    return data[:MAX_OUTPUT_BYTES].decode(errors="replace") + "\n[output clipped]"


def execute(targets, target, code, metrics, runtime="bash"):
    start = time.monotonic()
    stdout = stderr = ""
    try:
        stdout, stderr, code_status = targets.execute(target, code, runtime)
        result = {"stdout": show(stdout), "stderr": show(stderr), "exit_code": code_status}
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result = {"stdout": "", "stderr": f"transport error ({type(exc).__name__})", "exit_code": None}
    metrics.append({
        "endpoint": target, "runtime": runtime, "ms": round((time.monotonic() - start) * 1000, 3),
        "code_bytes": len(code.encode()), "visible_bytes": sum(len(result[f].encode()) for f in ("stdout", "stderr")),
        "stdout_bytes": len(stdout.encode()), "stderr_bytes": len(stderr.encode()),
        "exit_code": result["exit_code"],
    })
    return result


def replay(case, targets, metrics):
    for stage in case.actions:
        previous = ""
        for target, template in stage:
            code = template.replace(PREVIOUS_STDOUT, shlex.quote(previous))
            result = execute(targets, target, code, metrics)
            if result["exit_code"] != 0:
                return "reference_action_failed"
            previous = result["stdout"]
    return "completed"


def live(case, targets, mode, model, controller, metrics, decisions):
    schema = controller / "action-schema.json"
    schema.write_text(json.dumps(SCHEMA))
    history = []
    for stage, request in enumerate(case.stages, 1):
        history.append({"stage": stage, "request": request})
        for round_number in range(1, MAX_ROUNDS + 1):
            prompt = (
                "You control two isolated Linux targets named east and west. Actions start in /work. "
                "Only submit actions through the requested JSON response. Do not call tools or inspect this controller. "
                "The driver executes actions in order and returns stdout, stderr and exit status before another decision. "
                "Choose Bash or Python for each action. You may batch commands, filter output, and save files. "
                "A Bash action after another action in the same batch can use the literal {previous_stdout} "
                "as an unquoted shell word. It is replaced with the preceding action's stdout as one shell-quoted argument. "
                f"{MODE_HELP[mode]} "
                f"Use up to {MAX_ACTIONS} actions in this decision. Mark done when the current request will be complete "
                "after the listed actions. Leave the source fixtures unchanged except for requested edits. "
                f"Only solve the latest stage. Task and action history follows.\n{json.dumps(history, ensure_ascii=False)}"
            )
            decision, usage, error = model_action(controller, schema, model, prompt)
            decisions.append({"stage": stage, "decision": round_number, "usage": usage, "status": error or "ok"})
            if error:
                return error
            previous = ""
            for action in decision["actions"]:
                code = action["code"]
                if action["runtime"] == "bash":
                    code = code.replace(PREVIOUS_STDOUT, shlex.quote(previous))
                result = execute(targets, action["target"], code, metrics, action["runtime"])
                history.append({"action": action, "result": result})
                previous = result["stdout"]
                if result["exit_code"] is None:
                    return "transport_failure"
            if decision["done"]:
                break
        else:
            return "decision_budget_exhausted"
    return "completed"


def trial(case, binary, mode, kind, model):
    metrics, decisions = [], []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="one-remote-controller-") as controller:
        with RemoteTargets(binary, mode) as targets:
            install(case, targets.workspaces)
            setup_seconds = time.monotonic() - started
            setup_transport_calls = targets.transport_calls
            at = time.monotonic()
            status = (
                replay(case, targets, metrics)
                if kind == "replay" else live(case, targets, mode, model, Path(controller), metrics, decisions)
            )
            task_seconds = time.monotonic() - at
            checks = verify(case, targets.workspaces)
            usage = Counter()
            for decision in decisions:
                usage.update({k: decision["usage"][k] for k in USAGE_KEYS if k in decision["usage"]})
            return {
                "case": case.name, "mode": mode, "status": status, "checks": checks,
                "success": status == "completed" and bool(checks) and all(checks.values()),
                "setup_seconds": round(setup_seconds, 3), "task_seconds": round(task_seconds, 3),
                "setup_transport_calls": setup_transport_calls,
                "transport_calls": targets.transport_calls - setup_transport_calls,
                "actions": metrics, "decisions": decisions, "usage": dict(usage),
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("replay", "model"))
    parser.add_argument("--case", choices=[c.name for c in CASES], action="append")
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    cases = [case for case in CASES if not opts.case or case.name in opts.case]
    output = opts.output or ROOT / "experiments/results" / f"remote-{opts.kind}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    results = {
        "kind": opts.kind, "trials": [],
        "image": subprocess.check_output(["docker", "image", "inspect", "--format={{.Id}}", IMAGE], text=True).strip(),
        "mode_order_seed": 20261125,
    }
    if opts.kind == "model":
        results.update(
            model=opts.model, reasoning="low", max_rounds_per_stage=MAX_ROUNDS, max_actions_per_round=MAX_ACTIONS,
            codex=subprocess.check_output(["codex", "--version"], text=True).strip(),
        )
    order = random.Random(results["mode_order_seed"])
    with tempfile.TemporaryDirectory(prefix="one-remote-bin-") as temp:
        binary = Path(temp) / "one"
        subprocess.run(
            ["go", "build", "-mod=readonly", "-o", str(binary), "."], cwd=ROOT / "ssh-mcp",
            env={**os.environ, "CGO_ENABLED": "0"}, check=True, timeout=60,
        )
        for case in cases:
            for mode in order.sample(MODES, len(MODES)):
                row = trial(case, binary, mode, opts.kind, opts.model)
                results["trials"].append(row)
                temporary = output.with_suffix(".tmp")
                temporary.write_text(json.dumps(results, indent=2) + "\n")
                temporary.replace(output)
                print(case.name, mode, row["status"], "PASS" if row["success"] else "FAIL",
                      f'{len(row["actions"])} actions', row["usage"], flush=True)
    if opts.kind == "replay" and not all(row["success"] for row in results["trials"]):
        raise SystemExit("a reference task failed")
    print(output)


if __name__ == "__main__":
    main()
