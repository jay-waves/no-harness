"""Replay identical queries over four interpreter and data-reuse policies."""
import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
from time import perf_counter_ns

from remote_targets import IMAGE, RemoteTargets
from reuse_case import QUERIES, REGIONS, SIZES_MIB, seed


ROOT = Path(__file__).resolve().parents[1]
ARMS = ("ssh-reparse", "ssh-cache", "one-warm", "one-reset")
CHECKPOINTS = (1, 2, 4, 8)
IO_FIELDS = ("source_read_bytes", "cache_read_bytes", "cache_write_bytes")
TARGET = "east"

# The four arms share this implementation. A packed uint32 retains the group,
# region, flag and adjusted value for every cleaned, joined row.
REPLAY = r"""
from array import array
import csv
import io
import json
from pathlib import Path
from time import perf_counter_ns

def read_sources():
    with open("groups.csv", "rb") as handle:
        table = handle.read()
    dims = {int(group): (REGIONS.index(region), int(multiplier), int(active))
            for group, region, multiplier, active in
            list(csv.reader(io.StringIO(table.decode("ascii"))))[1:]}
    records = array("I")
    append = records.append
    with open("source.csv", "rb") as handle:
        for line in handle:
            value = int(line[16:28])
            region, multiplier, active = dims[int(line[11:15])]
            if value and active:
                adjusted = value * multiplier
                append((adjusted << 9) | (int(line[29:31]) << 8) |
                       (region << 6) | int(line[11:15]))
        read_bytes = handle.tell()
    return records, read_bytes + len(table)

def answer(records, query):
    if query in ("baseline", "flagged", "high_value", "west_small"):
        if query == "baseline":
            values = records
        elif query == "flagged":
            values = (record for record in records if record & 256)
        elif query == "high_value":
            values = (record for record in records if record >> 9 >= 25000)
        else:
            values = (record for record in records
                      if (record >> 6) & 7 == 3 and record >> 9 < 10000)
        count = total = 0
        for record in values:
            count += 1
            total += record >> 9
        return {"rows": count, "adjusted_sum": total}

    if query == "regions":
        totals = [0] * len(REGIONS)
        for record in records:
            totals[(record >> 6) & 3] += record >> 9
        return dict(zip(REGIONS, totals))

    groups = {}
    if query == "east_flagged":
        for record in records:
            if (record >> 6) & 7 == 5:
                group = record & 63
                groups[group] = groups.get(group, 0) + 1
    elif query == "group_max":
        for record in records:
            group = record & 63
            groups[group] = max(groups.get(group, 0), record >> 9)
    elif query == "region_winners":
        for record in records:
            group_region = record & 255
            groups[group_region] = groups.get(group_region, 0) + (record >> 9)
        return {name: max((key for key in groups if key >> 6 == region),
                          key=lambda key: (groups[key], -(key & 63))) & 63
                for region, name in enumerate(REGIONS)}
    else:
        raise ValueError(query)
    return groups

def run(arm, query):
    started = perf_counter_ns()
    source_read_bytes = cache_read_bytes = cache_write_bytes = 0
    path = Path("joined.uint32")
    if arm == "one-warm" and "_joined" in globals():
        records = _joined
    elif arm == "ssh-cache" and path.exists():
        records = array("I")
        with path.open("rb") as handle:
            records.fromfile(handle, path.stat().st_size // records.itemsize)
            cache_read_bytes = handle.tell()
    else:
        records, source_read_bytes = read_sources()
        if arm == "ssh-cache":
            with path.open("wb") as handle:
                records.tofile(handle)
                cache_write_bytes = handle.tell()
        if arm == "one-warm":
            globals()["_joined"] = records

    prepared_ns = perf_counter_ns() - started
    result = answer(records, query)
    print(json.dumps(dict(answer=result, source_read_bytes=source_read_bytes,
                          cache_read_bytes=cache_read_bytes, cache_write_bytes=cache_write_bytes,
                          prepare_ns=prepared_ns, query_ns=perf_counter_ns() - started - prepared_ns),
                     sort_keys=True))
"""


def cumulative(turns, count):
    prefix = turns[:count]
    counters = ("elapsed_ns", "reset_ns", "prepare_ns", "query_ns", *IO_FIELDS)
    result = {"turns": count, **{key: sum(row[key] for row in prefix) for key in counters}}
    for scope in ("target", "controller"):
        peak = f"{scope}_peak_kib"
        after = f"{scope}_after_kib"
        result[peak] = max((row["memory"][peak] for row in prefix if row["memory"][peak] is not None),
                           default=None)
        result[after] = prefix[-1]["memory"][after]
    for key in prefix[0]["transport"]:
        result[key] = sum(row["transport"][key] for row in prefix)
    return result


def run_trial(binary, arm, size_mib):
    mode = "ssh" if arm.startswith("ssh-") else "one"
    turns = []
    with RemoteTargets(binary, mode, instrument=True, targets=(TARGET,), runtimes=("python",)) as remote:
        workspace = remote.workspaces[TARGET]
        expected = seed(workspace, size_mib)
        script = workspace / "replay.py"
        script.write_text(f"REGIONS = {REGIONS!r}\n" + REPLAY)
        source_bytes = sum((workspace / name).stat().st_size for name in ("source.csv", "groups.csv"))
        idle = remote.memory_snapshot()
        for index, (query, _) in enumerate(QUERIES):
            reset_ns = 0
            before_reset = remote.transport_snapshot()
            if arm == "one-reset" and index:
                reset_start = perf_counter_ns()
                remote.reset_session(TARGET, "python")
                reset_ns = perf_counter_ns() - reset_start
            after_reset = remote.transport_snapshot()
            code = f'exec(open("replay.py").read()); run({arm!r}, {query!r})'
            stdout, stderr, status = remote.execute(TARGET, code, "python")
            if status:
                raise RuntimeError(f"{arm} {size_mib} MiB {query} failed ({status}): {stderr}")
            measured = json.loads(stdout)
            answer = measured.pop("answer")
            if json.dumps(answer, sort_keys=True) != json.dumps(expected[query], sort_keys=True):
                raise ValueError(f"{arm} {size_mib} MiB {query}: {answer!r} != {expected[query]!r}")
            if arm == "ssh-cache":
                cache_size = (workspace / "joined.uint32").stat().st_size
                expected_io = (0, cache_size, 0) if index else (source_bytes, 0, cache_size)
            else:
                expected_io = (0, 0, 0) if arm == "one-warm" and index else (source_bytes, 0, 0)
            if tuple(measured[field] for field in IO_FIELDS) != expected_io:
                raise RuntimeError(f"{arm} {query} produced invalid logical data I/O: {measured}")
            action = remote.last_action.copy()
            action["transport"] = {
                key: value + after_reset[key] - before_reset[key]
                for key, value in action["transport"].items()
            }
            turns.append({"query": query, "success": True, "answer": answer, **measured, **action,
                          "reset_ns": reset_ns, "elapsed_ns": reset_ns + action["action_ns"]})
    return {
        "arm": arm, "size_mib": size_mib, "source_bytes": source_bytes, "idle": idle, "turns": turns,
        "cumulative": [cumulative(turns, count) for count in CHECKPOINTS],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, action="append", choices=SIZES_MIB)
    parser.add_argument("--arm", action="append", choices=ARMS)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/results/reuse-fixed.json")
    args = parser.parse_args()
    sizes = args.size_mib or SIZES_MIB
    runs = [(size, arm) for size in sizes for arm in (args.arm or ARMS)]
    random.Random(20261124).shuffle(runs)
    data = {
        "suite": "reuse-fixed",
        "sizes_mib": list(sizes),
        "image": subprocess.check_output(["docker", "image", "inspect", "--format={{.Id}}", IMAGE],
                                         text=True).strip(),
        "target": TARGET,
        "trials": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="one-reuse-bin-") as temp:
        binary = Path(temp) / "one"
        subprocess.run(["go", "build", "-mod=readonly", "-o", str(binary), "."], cwd=ROOT / "ssh-mcp",
                       env={**os.environ, "CGO_ENABLED": "0"}, check=True, timeout=60)
        for size, arm in runs:
            trial = run_trial(binary, arm, size)
            data["trials"].append(trial)
            pending = args.output.with_suffix(".tmp")
            pending.write_text(json.dumps(data, indent=2) + "\n")
            pending.replace(args.output)
            end = trial["cumulative"][-1]
            print(f"{size} MiB {arm}: {len(trial['turns'])} passed, "
                  f"{end['elapsed_ns']/1e9:.2f}s action and reset time", flush=True)
    print(args.output)


if __name__ == "__main__":
    main()
