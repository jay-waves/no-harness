"""Run progressively disclosed tasks across the real remote execution routes."""
import argparse
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import tempfile
from time import perf_counter_ns

from interactive_case import CASE, install_fixtures, verify_stage
from remote_bench import (
    MAX_ACTIONS, MAX_ROUNDS, MODE_HELP, MODES, ROOT, SCHEMA, USAGE_KEYS, execute, model_action,
)
from remote_cases import PREVIOUS_STDOUT
from remote_targets import IMAGE, RemoteTargets

INTERACTIVE_SCHEMA = deepcopy(SCHEMA)
INTERACTIVE_SCHEMA["properties"]["question"] = {"type": "string"}
INTERACTIVE_SCHEMA["required"].append("question")
RELEASES = ("staged", "upfront")


def _prompt(mode, history, *, staged):
    return (
        "You control isolated Linux targets east and west. Actions start in /work. Only submit the "
        "requested JSON response; do not use controller tools. You may use Bash or Python, batch ordinary "
        "operations in an action, filter outputs, and save durable caches. For a later Bash action in "
        "the same batch, {previous_stdout} as an unquoted word inserts the previous stdout as one "
        "shell-quoted argument. Keep source fixtures and earlier answer artifacts intact. "
        f"{MODE_HELP[mode]} "
        f"You may submit up to {MAX_ACTIONS} actions in one decision. If a necessary user parameter is "
        "unavailable, set question to a specific question, done to true, and actions to an empty list. "
        "Otherwise leave question empty; mark done after your actions will satisfy the current request. "
        + ("Only act on the latest request. The operator may change the environment after a checkpoint. " if staged else "")
        + "The conversation and observed results follow.\n" + history
    )


def _snapshot(targets):
    return targets.memory_snapshot()


def _actions(targets, plan, metrics, history):
    previous = ""
    for action in plan:
        code = action["code"]
        if action["runtime"] == "bash":
            code = code.replace(PREVIOUS_STDOUT, shlex.quote(previous))
        result = execute(targets, action["target"], code, metrics, action["runtime"])
        metrics[-1].update(targets.last_action)
        metrics[-1]["code_lines"] = len(code.splitlines())
        history.append({"action": action, "result": result})
        if result["exit_code"] is None:
            return False
        previous = result["stdout"]
    return True


def _stage_metrics(number, stage, actions, decisions, before, after, checks, status):
    usage = Counter()
    for decision in decisions:
        usage.update({key: decision["usage"][key] for key in USAGE_KEYS if key in decision["usage"]})
    summed = {
        key: sum(a[key] for a in actions)
        for key in ("code_bytes", "stdout_bytes", "stderr_bytes", "visible_bytes")
    }
    transport = Counter()
    for action in actions:
        transport.update({key: value for key, value in action.get("transport", {}).items() if value is not None})
    memory = {}
    for scope in ("target", "controller"):
        field = f"{scope}_kib"
        initial, final = before.get(field), after.get(field)
        peaks = [a.get("memory", {}).get(f"{scope}_peak_kib") for a in actions] + [initial, final]
        memory.update({
            f"{scope}_before_kib": initial,
            f"{scope}_peak_kib": max((n for n in peaks if n is not None), default=None),
            f"{scope}_after_kib": final,
        })
    memory["model_cli_peak_rss_kib"] = max(
        (d["model_cli_peak_rss_kib"] for d in decisions if d.get("model_cli_peak_rss_kib") is not None),
        default=None,
    )
    return {
        "step": number, "name": stage.name, "status": status,
        "success": status == "completed" and bool(checks) and all(checks.values()),
        "checks": checks, "release_files": sum(map(len, stage.release.values())),
        "usage": dict(usage), "transport": dict(transport), "memory": memory,
        "actions": actions, "decisions": decisions, **summed,
    }


def checkpoint(stage, future, workspaces, visible, snapshots, question, actions):
    checks = verify_stage(stage, workspaces, visible, snapshots, question=question)
    if stage.clarification:
        checks["no_actions_while_waiting"] = not actions
    for pending in future:
        for endpoint, files in pending.release.items():
            for name in files:
                checks[f"unreleased_{endpoint}_{name}"] = not os.path.lexists(workspaces[endpoint] / name)
    return checks


def run_trial(binary, mode, release, kind, model):
    staged = release == "staged"
    visible = deepcopy(CASE.fixtures if staged else CASE.control_fixtures)
    stages = CASE.stages if staged else CASE.control_stages
    history, recorded, snapshots = [], [], {}
    with tempfile.TemporaryDirectory(prefix="one-interactive-controller-") as temp, RemoteTargets(binary, mode, instrument=True) as targets:
        controller = Path(temp)
        schema = controller / "action-schema.json"
        schema.write_text(json.dumps(INTERACTIVE_SCHEMA))
        memory_file = controller / "model-rss"
        install_fixtures(visible, targets.workspaces)
        idle = _snapshot(targets)
        for number, stage in enumerate(stages, 1):
            install_fixtures(stage.release, targets.workspaces)
            for endpoint, files in stage.release.items():
                visible.setdefault(endpoint, {}).update(files)
            before = _snapshot(targets)
            actions, decisions, checks = [], [], {}
            history.append({"step": number, "request": stage.request})
            upcoming = stages[number:]
            status = "decision_budget_exhausted"
            if kind == "replay":
                plan = [{"target": target, "runtime": "bash", "code": code} for target, code in stage.actions]
                if _actions(targets, plan, actions, history):
                    checks = checkpoint(stage, upcoming, targets.workspaces, visible, snapshots, stage.clarification, actions)
                    status = "completed" if checks and all(checks.values()) else "checkpoint_failed"
                else:
                    status = "transport_failure"
            else:
                for turn in range(1, MAX_ROUNDS + 1):
                    at = perf_counter_ns()
                    serialized = json.dumps(history, ensure_ascii=False)
                    history_encode_ns = perf_counter_ns() - at
                    prompt = _prompt(mode, serialized, staged=staged)
                    memory_file.unlink(missing_ok=True)
                    decision, usage, error = model_action(controller, schema, model, prompt, memory_file=memory_file)
                    try:
                        model_rss = int(memory_file.read_text().strip())
                    except (OSError, ValueError):
                        model_rss = None
                    decisions.append({
                        "round": turn, "usage": usage, "status": error or "ok",
                        "history_bytes": len(serialized.encode()), "history_encode_ns": history_encode_ns,
                        "prompt_bytes": len(prompt.encode()), "model_cli_peak_rss_kib": model_rss,
                    })
                    if error:
                        status = error
                        break
                    question = decision.get("question")
                    if not isinstance(question, str):
                        status = "invalid_model_response"
                        break
                    plan = decision["actions"]
                    if not _actions(targets, plan, actions, history):
                        status = "transport_failure"
                        break
                    if not decision["done"]:
                        continue
                    history.append({"agent_question": question} if question else {"agent": "done"})
                    checks = checkpoint(stage, upcoming, targets.workspaces, visible, snapshots, question, actions)
                    if checks and all(checks.values()):
                        status = "completed"
                        break
                    status = "checkpoint_failed"
                    history.append({"verifier": "Checkpoint incomplete. Recheck these requirements.",
                                    "failed_checks": [key for key, value in checks.items() if not value]})
            row = _stage_metrics(number, stage, actions, decisions, before, _snapshot(targets), checks, status)
            recorded.append(row)
            print(mode, release, number, stage.name, status, f"{len(actions)} actions", flush=True)
            if not row["success"]:
                break
        return {
            "mode": mode, "release": release, "idle_memory": idle,
            "status": recorded[-1]["status"], "expected_stages": len(stages),
            "success": len(recorded) == len(stages) and all(row["success"] for row in recorded),
            "stages": recorded,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("replay", "model"))
    parser.add_argument("--mode", choices=MODES, action="append")
    parser.add_argument("--release", choices=RELEASES, default="staged")
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    modes = args.mode or (list(MODES) if args.release == "staged" else ["docker"])
    rng = random.Random(20261125)
    rng.shuffle(modes)
    output = args.output or ROOT / "experiments/results" / f"interactive-{args.kind}-{args.release}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "suite": "interactive", "kind": args.kind, "case": CASE.name, "trials": [],
        "model": args.model if args.kind == "model" else None,
        "image": subprocess.check_output(["docker", "image", "inspect", "--format={{.Id}}", IMAGE], text=True).strip(),
        "limits": {"rounds_per_turn": MAX_ROUNDS, "actions_per_round": MAX_ACTIONS},
    }
    with tempfile.TemporaryDirectory(prefix="one-interactive-bin-") as temp:
        binary = Path(temp) / "one"
        subprocess.run(["go", "build", "-mod=readonly", "-o", str(binary), "."], cwd=ROOT / "ssh-mcp",
                       env={**os.environ, "CGO_ENABLED": "0"}, check=True, timeout=60)
        for mode in modes:
            row = run_trial(binary, mode, args.release, args.kind, args.model)
            data["trials"].append(row)
            pending = output.with_suffix(".tmp")
            pending.write_text(json.dumps(data, indent=2) + "\n")
            pending.replace(output)
            print(mode, args.release, "PASS" if row["success"] else "FAIL", flush=True)
    if args.kind == "replay" and not all(row["success"] for row in data["trials"]):
        raise SystemExit("interactive reference replay failed")
    print(output)


if __name__ == "__main__":
    main()
