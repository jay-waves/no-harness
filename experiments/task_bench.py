# /// script
# requires-python = ">=3.12"
# dependencies = ["tiktoken==0.12.0", "pyarrow==23.0.1"]
# ///
"""Paired task replay and a small optional model pilot."""
import argparse
import base64
import contextlib
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import tempfile
import time

import tiktoken

from databench_cases import make_databench_case
from humaneval_cases import make_humaneval_case
from task_cases import FAMILIES, install, make_case, replay_code, verify

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "no-harness-footprint:python"
MODES = ("fresh", "persistent")
POLICIES = ("full", "bounded")
PUBLIC_CASES = {"databench": make_databench_case, "humaneval": make_humaneval_case}
OUTPUT_CAP = 32 * 1024
RULE = "Use Python in the task workspace. Save each answer to its specified stage JSON file."


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=30, **kwargs).stdout.strip()


class Target:
    def __init__(self, directory, binary, mode):
        self.root = directory
        self.workspace = directory / "work"
        self.workspace.mkdir()
        (directory / "rpc").mkdir()
        self.mode = mode
        self.binary = binary
        self.container = self.connection = self.stream = None

    def start(self):
        args = [
            "docker", "run", "-d", "--rm", "--pull=never", "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "-w", "/work",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{self.workspace}:/work:Z",
        ]
        if self.mode == "fresh":
            self.container = run([*args, "--entrypoint", "/bin/sleep", IMAGE, "infinity"])
            return
        self.container = run([
            *args, "-v", f"{self.root / 'rpc'}:/rpc:Z", "-v", f"{self.binary}:/one:ro,Z",
            "-e", "XDG_CACHE_HOME=/rpc", "--entrypoint", "/one", IMAGE, "worker",
        ])
        path = self.root / "rpc" / "ssh-mcp" / "worker.sock"
        deadline = time.monotonic() + 5
        while not path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("worker socket did not start")
            time.sleep(.01)
        self.connection = socket.socket(socket.AF_UNIX)
        self.connection.settimeout(10)
        self.connection.connect(str(path))
        self.stream = self.connection.makefile("rwb")
        hello = self.rpc("hello", protocol=1)
        self.version = hello["version"]
        self.session_id = self.rpc("open", open={"runtime": "python"})["session"]["id"]

    def close(self):
        if self.stream is not None:
            self.stream.close()
        if self.connection is not None:
            self.connection.close()
        if self.container is not None:
            subprocess.run(["docker", "stop", "-t", "0", self.container], timeout=15, capture_output=True)

    def rpc(self, method, **fields):
        self.stream.write((json.dumps({"version": getattr(self, "version", ""), "method": method, **fields}) + "\n").encode())
        self.stream.flush()
        response = json.loads(self.stream.readline())
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response

    def fresh_process(self, code):
        return subprocess.run(
            ["docker", "exec", "-i", self.container, "python3", "-B", "-"],
            input=code, text=True, errors="replace", capture_output=True, timeout=10,
        )

    def execute(self, code):
        if self.mode == "fresh":
            result = self.fresh_process(code)
            return result.stdout, result.stderr, result.returncode
        execute = self.rpc("execute", execute={"session_id": self.session_id, "code": code})
        read = {"session_id": self.session_id, "execution_id": execute["execution"]["id"], "wait_ms": 500}
        deadline = time.monotonic() + 10
        stdout, stderr = [], []
        while time.monotonic() < deadline:
            out = self.rpc("read", read=read)["output"]
            for stream, chunks in (("stdout", stdout), ("stderr", stderr)):
                if out.get(stream + "_base64"):
                    chunks.append(base64.b64decode(out[stream + "_base64"]))
                else:
                    chunks.append(out.get(stream, "").encode())
            if out.get("truncated") or out.get("error"):
                raise RuntimeError("worker output truncated or lost")
            if out["output_closed"] and out.get("exit_code") is not None:
                return (
                    b"".join(stdout).decode(errors="replace"),
                    b"".join(stderr).decode(errors="replace"),
                    out["exit_code"],
                )
            read["cursor"] = out["cursor"]
        raise TimeoutError("worker execution exceeded 10 seconds")

    def hidden_check(self, code):
        try:
            result = self.fresh_process(code + "\nprint('__ONE_HIDDEN_PASS__')\n")
            return result.returncode == 0 and result.stdout.splitlines()[-1:] == ["__ONE_HIDDEN_PASS__"]
        except subprocess.TimeoutExpired:
            return False


@contextlib.contextmanager
def target_for(case, binary, mode):
    with tempfile.TemporaryDirectory(prefix="one-task-") as temp:
        target = Target(Path(temp), binary, mode)
        install(case, target.workspace)
        try:
            target.start()
            yield target
        finally:
            target.close()


def present_output(stdout, stderr, exit_code, policy):
    if len(stdout.encode()) + len(stderr.encode()) > OUTPUT_CAP:
        raise ValueError("output exceeds benchmark cap")
    lines = stdout.splitlines()
    shown = stdout
    if policy == "bounded" and len(lines) > 1:
        shown = lines[0] + "\n"
    return {"stdout": shown, "stderr": stderr, "exit_code": exit_code}


def replay_trial(case, binary, mode, policy, encoder):
    def count(text):
        return len(encoder.encode_ordinary(text))

    history = ["Instruction: " + RULE]
    cumulative = 0
    turns = []
    with target_for(case, binary, mode) as target:
        for step, prompt in enumerate(case.prompts, 1):
            history.append(f"User: {prompt} Save the answer in stage-{step}.json.")
            cumulative += count("\n".join(history))  # Prompt for code generation.
            code = replay_code(case, step, mode)
            start = time.monotonic()
            stdout, stderr, exit_code = target.execute(code)
            elapsed = 1000 * (time.monotonic() - start)
            if exit_code != 0 or stderr:
                raise ValueError(f"{case.name} {mode} turn {step} failed: {stderr[:1000]}")
            response = json.dumps(present_output(stdout, stderr, exit_code, policy), ensure_ascii=False)
            history.extend(("Tool call:\n" + code, "Tool response:\n" + response))
            cumulative += count("\n".join(history))  # Prompt for interpreting the tool response.
            history.append(f"Assistant: Saved stage-{step}.json.")
            turns.append({
                "step": step, "ms": elapsed, "code_tokens": count(code),
                "output_tokens": count(response), "cumulative_input_tokens": cumulative,
            })
        checks = verify(case, target.workspace, target.hidden_check)
        return {
            "family": case.family, "variant": case.variant, "mode": mode, "policy": policy,
            "fixture_bytes": sum(len(body.encode()) for body in case.fixtures.values()),
            "checks": checks, "turns": turns,
        }


def model_trial(case, binary, mode, model, encoder):
    """Ask for one action per newly revealed question and run it in the target."""
    with target_for(case, binary, mode) as target:
        host_dir = target.root / "controller"
        host_dir.mkdir()
        schema = target.root / "action.json"
        schema.write_text(json.dumps({
            "type": "object", "properties": {"code": {"type": "string"}},
            "required": ["code"], "additionalProperties": False,
        }))
        args = [
            "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--json", "--color", "never", "-s", "read-only",
            "-C", str(host_dir), "-m", model, "-c", 'model_reasoning_effort="low"',
            "--output-schema", str(schema), "--",
        ]
        state = (
            "Global variables and imports from earlier actions persist."
            if mode == "persistent" else "Each action starts a fresh interpreter. Files persist."
        )
        history = []
        turns = []
        status = "completed"
        start = time.monotonic()
        for step, request in enumerate(case.prompts, 1):
            instruction = f"{request} Save the JSON answer in stage-{step}.json and print the answer."
            history.append({"question": instruction})
            prompt = (
                f"{RULE} Your Python code will be executed by an external controller in /work. "
                "Do not use tools or inspect the local directory. Return only the JSON code action for the last question. "
                f"{state} The list below gives earlier decisions and their actual target outputs.\n"
                + json.dumps(history, ensure_ascii=False)
            )
            try:
                process = subprocess.run([*args, prompt], capture_output=True, text=True, timeout=60)
                events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
            except subprocess.TimeoutExpired:
                status = "provider_timeout"
                break
            usage = [event["usage"] for event in events if event.get("type") == "turn.completed"]
            items = [event["item"] for event in events if event.get("type") == "item.completed"]
            messages = [item["text"] for item in items if item["type"] == "agent_message"]
            if process.returncode or len(usage) != 1 or not messages:
                status = "provider_or_harness_failure"
                break
            if any(item["type"] in {"command_execution", "file_change", "mcp_tool_call", "web_search"} for item in items):
                status = "controller_tool_used"
                turns.append({"step": step, "usage": usage[0]})
                break
            row = {"step": step, "usage": usage[0]}
            turns.append(row)
            try:
                code = json.loads(messages[-1])["code"]
                if not isinstance(code, str) or len(code.encode()) > 16 * 1024:
                    raise ValueError("invalid code action")
            except (KeyError, TypeError, ValueError):
                status = "invalid_code_action"
                break
            at = time.monotonic()
            try:
                response = present_output(*target.execute(code), "full")
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                response = {"stdout": "", "stderr": str(exc)[:500], "exit_code": None}
                status = "target_failure"
            elapsed = 1000 * (time.monotonic() - at)
            row.update(
                code_tokens=len(encoder.encode_ordinary(code)),
                output_tokens=len(encoder.encode_ordinary(json.dumps(response, ensure_ascii=False))),
                ms=elapsed, exit_code=response["exit_code"],
            )
            history[-1].update(code=code, response=response)
            if status != "completed":
                break
        checks = verify(case, target.workspace, target.hidden_check)
        return {
            "family": case.family, "variant": case.variant, "mode": mode, "status": status,
            "seconds": time.monotonic() - start, "checks": checks, "turns": turns,
        }


def write_results(path, results):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(results, indent=2) + "\n")
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("replay", "model"))
    parser.add_argument("--variants", type=int, choices=range(1, 7))
    parser.add_argument("--family", choices=(*FAMILIES, *PUBLIC_CASES), action="append")
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    families = opts.family or FAMILIES
    path = opts.output or ROOT / "experiments/results" / f"task-{opts.kind}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    results = {"kind": opts.kind, "trials": []}
    results["image_id"] = run(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE])
    results["go"] = run(["go", "version"])
    results["docker"] = run(["docker", "version", "--format", "{{.Client.Version}}/{{.Server.Version}}"])
    results["tokenizer"] = "tiktoken 0.12.0 / o200k_base"
    encoder = tiktoken.get_encoding("o200k_base")
    if opts.kind == "model":
        results.update(model=opts.model, codex=run(["codex", "--version"]), reasoning="low", max_actions_per_stage=1)
    order = random.Random(20261124)
    with tempfile.TemporaryDirectory(prefix="one-task-build-") as tmp:
        binary = Path(tmp) / "one"
        run(["go", "build", "-mod=readonly", "-o", str(binary), "."],
            cwd=ROOT / "ssh-mcp", env={**os.environ, "CGO_ENABLED": "0"})
        for family in families:
            public = PUBLIC_CASES.get(family)
            variants = min(opts.variants or 2, 2) if public else opts.variants or (6 if opts.kind == "replay" else 1)
            for variant in range(1, variants + 1):
                case = public(variant) if public else make_case(family, variant)
                conditions = [(mode, policy) for mode in MODES for policy in POLICIES] if opts.kind == "replay" else list(MODES)
                order.shuffle(conditions)
                for condition in conditions:
                    if opts.kind == "replay":
                        mode, policy = condition
                        trial = replay_trial(case, binary, mode, policy, encoder)
                    else:
                        trial = model_trial(case, binary, condition, opts.model, encoder)
                    results["trials"].append(trial)
                    write_results(path, results)
                    print(case.name, condition, "pass" if all(trial["checks"].values()) else "fail", flush=True)
    if opts.kind == "replay" and not all(all(trial["checks"].values()) for trial in results["trials"]):
        raise SystemExit("one or more reference trajectories failed the verifier")
    print(path)


if __name__ == "__main__":
    main()
