"""Compare agent-generated Python through SSH and ∅ne on evolving contracts."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile

import tiktoken

from evolution_cases import CASES
from remote_bench import MAX_ACTIONS, ROOT, USAGE_KEYS, execute, model_action
from remote_targets import RemoteTargets
from reuse_live import LIVE_SCHEMA

MODES = ("ssh", "one")
MAX_DECISIONS = 3


def verify(target, mode, examples):
    prefix = "from solution import solve\n" if mode == "ssh" else ""
    # Keep sample objects and verifier names out of the agent's namespace.
    probe = prefix + f'print(repr([solve(item) for item in {[x for x, _ in examples]!r}]))'
    out, err, status = target.execute("east", probe, "python")
    if status:
        return [False] * len(examples), err[-500:]
    import ast
    try:
        actual = ast.literal_eval(out)
        if not isinstance(actual, list) or len(actual) != len(examples):
            raise ValueError("unexpected result count")
        return [
            json.dumps(answer, sort_keys=True) == json.dumps(expected, sort_keys=True)
            for answer, (_, expected) in zip(actual, examples, strict=True)
        ], ""
    except (TypeError, ValueError, SyntaxError):
        return [False] * len(examples), "Output could not be read as the required result."


def trial(binary, case, mode, pair, model):
    encoder = tiktoken.get_encoding("o200k_base")
    contract = CASES[case]
    execution = (
        "Each action starts fresh Python on a reused SSH connection. Files persist. "
        "Expose solve via solution.py (the grader imports it in a new process)."
        if mode == "ssh" else
        "All actions run in one persistent Python REPL. Files also persist. "
        "Expose solve in the current Python namespace (it may be imported from a file)."
    )
    history, stages = [], []
    with tempfile.TemporaryDirectory() as temp, RemoteTargets(
        binary, mode, targets=("east",), runtimes=("python",),
    ) as remote:
        here = Path(temp)
        schema = here / "action-schema.json"
        schema.write_text(json.dumps(LIVE_SCHEMA))
        for step, (requirement, _, _) in enumerate(contract, 1):
            history.append({"checkpoint": step, "requirement": requirement})
            usage, actions, decisions = Counter(), [], []
            checks = [False] * step
            for _ in range(MAX_DECISIONS):
                prompt = (
                    f"Extend your Python implementation of solve(data) for the {case} task. "
                    f"{execution} The work directory is /work and starts empty. Use only Python remote actions "
                    f"on target east, submitted in the JSON response (at most {MAX_ACTIONS} each decision). "
                    "Do not call controller tools. Return JSON-compatible Python values. Your current implementation "
                    "must pass all requirements disclosed so far. Hidden inputs are not provided. "
                    "Extend your earlier implementation; you may edit files or redefine functions. "
                    "Mark done when ready for the grader.\n" + json.dumps(history)
                )
                plan, used, error = model_action(here, schema, model, prompt)
                usage.update({key: used[key] for key in USAGE_KEYS if key in used})
                decisions.append({"usage": used, "error": error})
                if error:
                    break
                if any(a["target"] != "east" or a["runtime"] != "python" for a in plan["actions"]):
                    decisions[-1]["error"] = "action must use east Python"
                    break
                for action in plan["actions"]:
                    code = action["code"]
                    response = execute(remote, "east", code, actions, "python")
                    actions[-1].update(code=code, tokens=len(encoder.encode_ordinary(code)))
                    history.append({"submitted_python": code, "response": response})
                if plan["done"]:
                    checks, feedback = verify(remote, mode, [(x, y) for _, x, y in contract[:step]])
                    history.append({"current_pass": checks[-1], "previous_cases_passed": sum(checks[:-1]),
                                    "previous_cases_tested": step - 1, "error": feedback})
                    if all(checks):
                        break
            stages.append({"step": step, "checks": checks, "success": all(checks), "actions": actions,
                           "usage": dict(usage), "decisions": decisions,
                           "command_tokens": sum(a["tokens"] for a in actions)})
            print(case, mode, pair, step, checks, stages[-1]["command_tokens"], flush=True)
    return {"task": case, "mode": mode, "pair": pair, "stages": stages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/results/evolution.json")
    opts = parser.parse_args()
    if opts.pairs < 1:
        parser.error("--pairs must be positive")
    summary = {"suite": "evolution", "model": opts.model, "reasoning": "low",
               "encoding": "o200k_base", "pairs": opts.pairs, "trials": []}
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, ThreadPoolExecutor(max_workers=6) as pool:
        binary = Path(temp) / "one"
        subprocess.run(["go", "build", "-mod=readonly", "-o", str(binary), "."], cwd=ROOT / "ssh-mcp",
                       env={**os.environ, "CGO_ENABLED": "0"}, check=True)
        from concurrent.futures import as_completed
        futures = [pool.submit(trial, binary, case, mode, pair, opts.model)
                   for pair in range(1, opts.pairs + 1) for case in CASES for mode in MODES]
        for future in as_completed(futures):
            summary["trials"].append(future.result())
            pending = opts.output.with_suffix(".tmp")
            pending.write_text(json.dumps(summary, indent=2) + "\n")
            pending.replace(opts.output)


if __name__ == "__main__":
    main()
