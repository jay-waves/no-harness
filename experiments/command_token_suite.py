"""Experiment 1: matched four-turn tasks on multiplexed SSH and a persistent REPL."""
import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from itertools import accumulate

import matplotlib.pyplot as plt
import tiktoken

from remote_targets import RemoteTargets
from reuse_case import QUERIES, seed
from reuse_command_tokens import ROOT, SETUP, EXPRESSIONS


@dataclass
class Case:
    name: str
    setup: str
    expressions: list
    expected: list


def cases(work):
    joined = seed(work, 8)
    (work / "activity.log").write_text("web 200\napi 503\nweb 404\napi 200\napi 502\n")
    (work / "deps.json").write_text('{"app":["api","web"],"api":["db"],"web":["api"],"db":[]}')
    (work / "settings.ini").write_text("[service]\nworkers=4\ntimeout=30\nregion=east\n")
    (work / "files").mkdir()
    for name, body in (("a.txt", "abc"), ("b.txt", "de"), ("c.log", "f")):
        (work / "files" / name).write_text(body)
    return [
        Case("CSV join", SETUP, EXPRESSIONS, [joined[q] for q, _ in QUERIES[:4]]),
        Case("HTTP log", 'events = [line.split() for line in open("activity.log")]\n',
             ['len(events)', 'sum(int(s) >= 500 for _, s in events)',
              'sorted({name for name, s in events if int(s) >= 400})',
              'sum(name == "api" and int(s) >= 500 for name, s in events)'],
             [5, 2, ["api", "web"], 2]),
        Case("Dependencies", '''import json
graph = json.load(open("deps.json"))
def closure(node):
    seen = set()
    pending = list(graph[node])
    while pending:
        item = pending.pop()
        if item not in seen:
            seen.add(item)
            pending.extend(graph[item])
    return sorted(seen)
''', ['closure("app")', 'closure("web")', 'sorted(n for n in graph if "db" in closure(n))',
       'sorted(n for n in graph if not graph[n])'],
             [["api", "db", "web"], ["api", "db"], ["api", "app", "web"], ["db"]]),
        Case("Configuration", '''from configparser import ConfigParser
cfg = ConfigParser()
cfg.read("settings.ini")
svc = cfg["service"]
''', ['svc.getint("workers")', 'svc.getint("timeout")', 'svc["region"]',
       'svc.getint("workers") * svc.getint("timeout")'], [4, 30, "east", 120]),
        Case("File inventory", '''from pathlib import Path
files = sorted(Path("files").iterdir())
''', ['len(files)', 'sum(p.stat().st_size for p in files)', '[p.name for p in files if p.suffix == ".txt"]',
       'max(files, key=lambda p: p.stat().st_size).name'], [3, 6, ["a.txt", "b.txt"], "a.txt"]),
        Case("Independent commands", "", ['len("east")', 'sum(range(6))', 'min([9, 4, 7])', 'hex(255)'],
             [4, 15, 4, "0xff"]),
    ]


def commands(case, mode, count):
    queries = [f"print(repr({expr}))" for expr in case.expressions]
    if mode == "REPL":
        return "retain", [case.setup + queries[0], *queries[1:]]
    inline = [case.setup + q for q in queries]
    imported = ["from helper import *\n" + q for q in queries]
    imported[0] = f'open("helper.py", "w").write({case.setup!r})\n' + imported[0]
    return min((("inline", inline), ("helper", imported)),
               key=lambda pair: sum(count(cmd) for cmd in pair[1]))


def run(binary, mode, index, count):
    with RemoteTargets(binary, "ssh" if mode == "SSH" else "one",
                       targets=("east",), runtimes=("python",)) as target:
        case = cases(target.workspaces["east"])[index]
        strategy, code = commands(case, mode, count)
        turns = []
        for cmd, expected in zip(code, case.expected, strict=True):
            probe = "import os, sys\nprint((os.getpid(), os.getenv('SSH_CONNECTION')), file=sys.stderr)"
            out, err, status = target.execute("east", cmd + "\n" + probe, "python")
            if status or ast.literal_eval(out) != expected:
                raise RuntimeError((case.name, mode, out, err))
            pid, connection = ast.literal_eval(err.strip())
            turns.append(dict(code=cmd, tokens=count(cmd), pid=pid, connection=connection, answer=expected))
        assert len({t["pid"] for t in turns}) == (4 if mode == "SSH" else 1)
        if mode == "SSH":
            assert turns[0]["connection"] and len({t["connection"] for t in turns}) == 1
        return case.name, dict(strategy=strategy, turns=turns)


def main():
    path = ROOT / "experiments/results"
    path.mkdir(exist_ok=True)
    encoder = tiktoken.get_encoding("o200k_base")
    def count(code):
        return len(encoder.encode_ordinary(code))
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "one"
        subprocess.run(["go", "build", "-mod=readonly", "-o", str(binary), "."],
                       cwd=ROOT / "ssh-mcp", env={**os.environ, "CGO_ENABLED": "0"}, check=True)
        for i in range(6):
            for mode in ("SSH", "REPL"):
                name, row = run(binary, mode, i, count)
                results.setdefault(name, {})[mode] = row
    (path / "experiment-one.json").write_text(json.dumps(results, indent=2) + "\n")
    fig, axes = plt.subplots(2, 3, figsize=(11, 6.2), sharex=True, sharey=True)
    for ax, (name, pair) in zip(axes.flat, results.items(), strict=True):
        for mode, marker, style in (("SSH", "o", "-"), ("REPL", "x", "--")):
            totals = list(accumulate(t["tokens"] for t in pair[mode]["turns"]))
            ax.plot(range(1, 5), totals, marker=marker, linestyle=style, label=mode)
            print(name, mode, pair[mode]["strategy"], totals)
        ax.set(title=name, xticks=range(1, 5), ylim=(0, None))
        ax.grid(axis="y", alpha=.25)
    fig.legend(*axes.flat[0].get_legend_handles_labels(), loc="upper center", ncols=2)
    fig.supxlabel("Questions answered")
    fig.supylabel("Cumulative submitted Python tokens")
    fig.tight_layout(rect=(.018, .025, 1, .94))
    for extension in ("png", "svg"):
        fig.savefig(path / f"experiment-one.{extension}", dpi=150)


if __name__ == "__main__":
    main()
