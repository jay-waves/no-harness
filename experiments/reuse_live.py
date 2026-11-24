# /// script
# requires-python = ">=3.12"
# dependencies = ["tiktoken==0.12.0"]
# ///
"""Compare agent-selected reuse strategies over direct SSH and production ∅ne MCP."""

import argparse
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
from time import monotonic

import tiktoken

from remote_bench import MAX_ACTIONS, MAX_ROUNDS, MODE_HELP, ROOT, SCHEMA, USAGE_KEYS, execute, model_action
from remote_targets import IMAGE, RemoteTargets
from reuse_case import QUERIES, SCHEMA as FIXTURE_SCHEMA, SIZES_MIB, answer_matches, fingerprints, seed

MODES = ("ssh", "one")
ORDER_SEED = 20261125
LIVE_SCHEMA = deepcopy(SCHEMA)
LIVE_SCHEMA["properties"]["actions"]["items"]["properties"]["target"]["enum"] = ["east"]


def run_stage(targets, mode, workspace, controller, schema, model, encoder, history, number, query, expected):
    name, question = query
    artifact = f"answer-{number}.json"
    history.append({"stage": number, "request": f"{question} Write the JSON answer to {artifact}."})
    actions, decisions, usage = [], [], Counter()
    status = "decision_budget_exhausted"
    for turn in range(1, MAX_ROUNDS + 1):
        prompt = (
            "You control one isolated Linux target named east. The starting directory is /work. "
            "Submit remote actions through the requested JSON response. Do not call controller tools. "
            "Actions run in order and stdout, stderr and exit status become available before the next decision. "
            f"{MODE_HELP[mode]} {FIXTURE_SCHEMA} "
            "Choose Bash or Python for each action. You may batch operations, filter output, retain files, or use "
            "any other available execution strategy. Preserve source.csv, groups.csv and earlier answer files. "
            f"Solve only the latest request and submit up to {MAX_ACTIONS} actions in each decision. "
            "Mark done when the actions in your decision will finish the current request. "
            "The driver reveals additional questions one at a time.\n"
            + json.dumps(history, ensure_ascii=False)
        )
        start = monotonic()
        decision, used, error = model_action(controller, schema, model, prompt)
        usage.update({key: used[key] for key in USAGE_KEYS if key in used})
        decisions.append({"turn": turn, "usage": used, "status": error or "ok", "seconds": monotonic() - start})
        if error:
            status = error
            break
        if any(action["target"] != "east" for action in decision["actions"]):
            status = "invalid_model_response"
            break
        for action in decision["actions"]:
            code = action["code"]
            result = execute(targets, "east", code, actions, action["runtime"])
            actions[-1]["code"] = code
            actions[-1]["command_tokens"] = len(encoder.encode_ordinary(code))
            actions[-1]["visible_output_tokens"] = sum(
                len(encoder.encode_ordinary(result[key])) for key in ("stdout", "stderr")
            )
            history.append({"action": action, "result": result})
        if decision["done"]:
            matched = answer_matches(workspace / artifact, expected)
            history.append({"checkpoint": "passed" if matched else "incorrect or missing JSON answer"})
            if matched:
                status = "completed"
                break
            status = "checkpoint_failed"
    return {
        "step": number, "name": name, "status": status, "success": status == "completed",
        "answer_verified": answer_matches(workspace / artifact, expected),
        "decisions": decisions, "actions": actions, "usage": dict(usage),
        "command_tokens": sum(action["command_tokens"] for action in actions),
        "visible_output_tokens": sum(action["visible_output_tokens"] for action in actions),
    }


def run_trial(binary, mode, pair, size_mib, model, encoder):
    with tempfile.TemporaryDirectory(prefix="one-reuse-controller-") as temp, RemoteTargets(binary, mode, targets=("east",)) as targets:
        controller = Path(temp)
        workspace = targets.workspaces["east"]
        schema = controller / "action-schema.json"
        schema.write_text(json.dumps(LIVE_SCHEMA))
        expected = seed(workspace, size_mib)
        inputs = fingerprints(workspace)
        fixture_bytes = sum((workspace / name).stat().st_size for name in inputs)
        history, stages = [], []
        for number, query in enumerate(QUERIES, 1):
            row = run_stage(targets, mode, workspace, controller, schema, model, encoder, history,
                            number, query, expected[query[0]])
            stages.append(row)
            print(f"pair {pair} {mode} stage {number}: {row['status']} "
                  f"({row['command_tokens']} command tokens)", flush=True)
        fixture_intact = fingerprints(workspace) == inputs
        return {
            "pair": pair, "mode": mode, "fixture_bytes": fixture_bytes, "fixture_sha256": inputs,
            "fixture_intact": fixture_intact, "stages": stages,
            "success": fixture_intact and all(stage["success"] for stage in stages),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--size-mib", type=int, choices=SIZES_MIB, default=max(SIZES_MIB))
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/results/reuse-live.json")
    opts = parser.parse_args()
    if opts.pairs < 1:
        parser.error("--pairs must be positive")
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    encoder = tiktoken.get_encoding("o200k_base")
    order = random.Random(ORDER_SEED)
    results = {
        "suite": "reuse-live", "model": opts.model, "reasoning": "low", "pairs": opts.pairs,
        "size_mib": opts.size_mib, "order_seed": ORDER_SEED, "encoding": encoder.name,
        "max_decisions_per_stage": MAX_ROUNDS, "max_actions_per_decision": MAX_ACTIONS,
        "codex": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "image": subprocess.check_output(["docker", "image", "inspect", "--format={{.Id}}", IMAGE], text=True).strip(),
        "trials": [],
    }
    with tempfile.TemporaryDirectory(prefix="one-reuse-bin-") as temp:
        binary = Path(temp) / "one"
        subprocess.run(
            ["go", "build", "-mod=readonly", "-o", str(binary), "."], cwd=ROOT / "ssh-mcp",
            env={**os.environ, "CGO_ENABLED": "0"}, check=True, timeout=60,
        )
        for pair in range(1, opts.pairs + 1):
            for mode in order.sample(MODES, len(MODES)):
                results["trials"].append(run_trial(binary, mode, pair, opts.size_mib, opts.model, encoder))
                temporary = opts.output.with_suffix(".tmp")
                temporary.write_text(json.dumps(results, indent=2) + "\n")
                temporary.replace(opts.output)
    print(opts.output)


if __name__ == "__main__":
    main()
