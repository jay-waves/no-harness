"""Count submitted Python tokens across multiplexed SSH and a persistent remote Python."""
import ast
import json
import os
from itertools import accumulate
from pathlib import Path
import subprocess
import tempfile

import matplotlib.pyplot as plt
import tiktoken

from remote_targets import RemoteTargets
from reuse_case import QUERIES, seed

ROOT = Path(__file__).resolve().parents[1]
SETUP = '''import csv
import json
with open("groups.csv") as f:
    groups = {int(r["group"]): (r["region"], int(r["multiplier"]))
              for r in csv.DictReader(f) if int(r["active"])}
with open("source.csv") as f:
    rows = [(groups[g][0], value * groups[g][1], flag)
            for _, g, value, flag in (map(int, r) for r in csv.reader(f))
            if g in groups and value]
def summary(values):
    return {"rows": len(values), "adjusted_sum": sum(values)}
'''
EXPRESSIONS = [
    'summary([v for _, v, _ in rows])',
    'summary([v for _, v, flag in rows if flag])',
    '{r: sum(v for region, v, _ in rows if region == r) for r in sorted({row[0] for row in rows})}',
    'summary([v for _, v, _ in rows if v >= 25000])',
]
ARMS = ("SSH inline", "SSH saved helper", "Persistent REPL", "Reset REPL")


def run(binary, arm, encoder):
    ssh = arm.startswith("SSH")
    turns = []
    with RemoteTargets(binary, "ssh" if ssh else "one", targets=("east",), runtimes=("python",)) as target:
        expected = seed(target.workspaces["east"], 8)
        for i, expression in enumerate(EXPRESSIONS):
            setup = SETUP
            if arm == "SSH saved helper":
                setup = (f'open("joined.py", "w").write({SETUP!r})\n' if i == 0 else "") + "from joined import rows, summary, json\n"
            elif arm == "Persistent REPL" and i:
                setup = ""
            elif arm == "Reset REPL" and i:
                target.reset_session("east", "python")
            code = setup + f"print(json.dumps({expression}, sort_keys=True))"
            probe = "import os as _probe_os\nprint((_probe_os.getpid(), _probe_os.getenv('SSH_CONNECTION')), file=__import__('sys').stderr)"
            # Keep the measurement probe outside the model-visible command and answer.
            stdout, stderr, status = target.execute("east", code + "\n" + probe, "python")
            if status or json.loads(stdout) != expected[QUERIES[i][0]]:
                raise ValueError((arm, i, status, stdout, stderr))
            pid, connection = ast.literal_eval(stderr.strip())
            turns.append(dict(code=code, tokens=len(encoder.encode_ordinary(code)), pid=pid,
                              ssh_connection=connection, answer=json.loads(stdout)))
    pids = {turn["pid"] for turn in turns}
    assert len(pids) == (1 if arm == "Persistent REPL" else len(turns))
    if ssh:
        connections = {turn["ssh_connection"] for turn in turns}
        assert len(connections) == 1 and None not in connections
    return turns


def main():
    path = ROOT / "experiments/results"
    path.mkdir(exist_ok=True)
    encoder = tiktoken.get_encoding("o200k_base")
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "one"
        subprocess.run(["go", "build", "-mod=readonly", "-o", str(binary), "."],
                       cwd=ROOT / "ssh-mcp", env={**os.environ, "CGO_ENABLED": "0"}, check=True)
        results = {arm: run(binary, arm, encoder) for arm in ARMS}
    (path / "reuse-command-tokens.json").write_text(json.dumps(results, indent=2) + "\n")
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    for arm, marker, style in zip(ARMS, ("o", "s", "^", "x"), ("-", "--", "-.", ":")):
        totals = list(accumulate(t["tokens"] for t in results[arm]))
        ax.plot(range(1, 5), totals, marker=marker, linestyle=style, label=arm)
        print(arm, totals)
    ax.set(xlabel="Queries answered", ylabel="Cumulative submitted Python tokens", xticks=range(1, 5))
    ax.legend()
    ax.grid(axis="y", alpha=.25)
    fig.tight_layout()
    fig.savefig(path / "reuse-command-tokens.svg")


if __name__ == "__main__":
    main()
