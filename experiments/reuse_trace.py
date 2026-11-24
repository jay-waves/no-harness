"""Replay captured reuse tasks with RSS sampling and fixture file-open events."""
import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from time import monotonic, sleep

from remote_targets import IMAGE, RemoteTargets
from reuse_case import QUERIES, answer_matches, fingerprints, seed


ROOT = Path(__file__).resolve().parent
INPUT_NAMES = ("source.csv", "groups.csv")
CHECKPOINTS = (1, 2, 4, 8)
IN_OPEN = 0x20
IN_CLOSE = 0x18
IN_Q_OVERFLOW = 0x4000
EVENT = struct.Struct("iIII")


@contextmanager
def watch_files(workspace):
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    try:
        watches = {}
        for name in INPUT_NAMES:
            # Close events delimit sequential opens before the queue is drained.
            descriptor = libc.inotify_add_watch(fd, os.fsencode(workspace / name), IN_OPEN | IN_CLOSE)
            if descriptor < 0:
                raise OSError(ctypes.get_errno(), f"inotify_add_watch {name}")
            watches[descriptor] = name

        def drain():
            counts = dict.fromkeys(INPUT_NAMES, 0)
            while True:
                try:
                    events = os.read(fd, 65536)
                except BlockingIOError:
                    return counts
                offset = 0
                while offset < len(events):
                    descriptor, mask, _, length = EVENT.unpack_from(events, offset)
                    offset += EVENT.size + length
                    if mask & IN_Q_OVERFLOW:
                        raise RuntimeError("inotify event queue overflow")
                    if mask & IN_OPEN:
                        counts[watches[descriptor]] += 1

        yield drain
    finally:
        os.close(fd)


def summarize(stages, count):
    prefix = stages[:count]
    actions = [action for stage in prefix for action in stage["actions"]]
    summary = {"turns": count, "actions": len(actions),
               "action_ns": sum(action["action_ns"] for action in actions)}
    summary["open_events"] = {
        name: sum(action["open_events"][name] for action in actions) for name in INPUT_NAMES
    }
    summary["actions_opening"] = {
        name: sum(bool(action["open_events"][name]) for action in actions) for name in INPUT_NAMES
    }
    for scope in ("target", "controller"):
        peak = f"{scope}_peak_kib"
        summary[peak] = max((action["memory"][peak] for action in actions
                             if action["memory"][peak] is not None), default=None)
        summary[f"{scope}_after_kib"] = prefix[-1]["after"][f"{scope}_kib"]
    return summary


def replay(binary, trial, size):
    stages = []
    with RemoteTargets(binary, trial["mode"], instrument=True, targets=("east",)) as remote:
        workspace = remote.workspaces["east"]
        expected = seed(workspace, size)
        source_hashes = fingerprints(workspace)
        if source_hashes != trial["fixture_sha256"]:
            raise RuntimeError("Regenerated fixture does not match live hashes")
        idle = remote.memory_snapshot()
        if [stage["name"] for stage in trial["stages"]] != [name for name, _ in QUERIES]:
            raise ValueError("Unexpected live query order")
        with watch_files(workspace) as drain:
            for stage in trial["stages"]:
                actions = []
                for original in stage["actions"]:
                    _, _, status = remote.execute(original["endpoint"], original["code"],
                                                  original["runtime"])
                    actions.append({
                        "runtime": original["runtime"], "exit_code": status,
                        "live_exit_code": original["exit_code"], "open_events": drain(),
                        **remote.last_action,
                    })
                matches = answer_matches(workspace / f"answer-{stage['step']}.json", expected[stage["name"]])
                stages.append({"step": stage["step"], "name": stage["name"], "success": matches,
                               "live_success": stage["answer_verified"], "actions": actions,
                               "after": remote.memory_snapshot()})
        fixture_intact = fingerprints(workspace) == source_hashes
    outcomes_match = fixture_intact == trial["fixture_intact"] and all(
        stage["success"] == stage["live_success"] and
        all(action["exit_code"] == action["live_exit_code"] for action in stage["actions"])
        for stage in stages
    )
    return {"pair": trial["pair"], "mode": trial["mode"], "stages": stages, "idle": idle,
            "fixture_intact": fixture_intact, "outcomes_match": outcomes_match,
            "cumulative": [summarize(stages, count) for count in CHECKPOINTS]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results/reuse-live.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results/reuse-trace.json")
    parser.add_argument("--wait", action="store_true", help="Wait for remaining live trajectories.")
    args = parser.parse_args()
    first = json.loads(args.input.read_text())
    if first["suite"] != "reuse-live":
        parser.error("Input must contain reuse-live results")
    image = subprocess.check_output(["docker", "image", "inspect", "--format={{.Id}}", IMAGE],
                                    text=True).strip()
    if image != first["image"]:
        raise RuntimeError("Replay image does not match the live image")
    data = {"suite": "reuse-trace", "size_mib": first["size_mib"], "image": image,
            "model": first["model"], "trials": []}
    seen = set()
    last_update = monotonic()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="one-reuse-trace-bin-") as temp:
        binary = Path(temp) / "one"
        subprocess.run(["go", "build", "-mod=readonly", "-o", str(binary), "."], cwd=ROOT.parent / "ssh-mcp",
                       env={**os.environ, "CGO_ENABLED": "0"}, check=True, timeout=60)
        while True:
            live = json.loads(args.input.read_text())
            for trial in live["trials"]:
                key = trial["pair"], trial["mode"]
                if key in seen:
                    continue
                result = replay(binary, trial, data["size_mib"])
                data["trials"].append(result)
                seen.add(key)
                pending = args.output.with_suffix(".tmp")
                pending.write_text(json.dumps(data, indent=2) + "\n")
                pending.replace(args.output)
                last_update = monotonic()
                print(f"pair {key[0]} {key[1]}: outcomes {'match' if result['outcomes_match'] else 'differ'}",
                      flush=True)
            if not args.wait or len(seen) >= 2 * first["pairs"]:
                break
            if monotonic() - last_update > 600:
                raise TimeoutError("No newly completed live trajectories for 10 minutes")
            sleep(3)
    if not data["trials"] or not all(trial["outcomes_match"] for trial in data["trials"]):
        raise SystemExit("Replay did not reproduce all captured outcomes")
    print(args.output)


if __name__ == "__main__":
    main()
