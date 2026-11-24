"""Fixtures, scripted smoke actions, and artifact checks for the remote agent pilot."""

import csv
import io
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from task_cases import read_artifact

# The smoke runner shell-quotes the preceding action output before replacing this token.
PREVIOUS_STDOUT = "{previous_stdout}"


@dataclass(frozen=True)
class RemoteCase:
    name: str
    fixtures: dict[str, dict[str, str]]
    stages: tuple[str, ...]
    actions: tuple[tuple[tuple[str, str], ...], ...]
    expected: tuple[dict, ...]
    edited: tuple[tuple[str, str, dict], ...] = ()


def _python(source):
    return f"python3 - <<'REMOTE_PY'\n{source.strip()}\nREMOTE_PY"


def _csv(header, rows):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue()


def _logs():
    rng = random.Random(3107)
    services = ("billing", "catalog", "search")
    endpoints = ("east", "west")
    records = {
        target: [
            (rng.choice(services), rng.choice((200, 200, 404, 429, 500, 502, 503)))
            for _ in range(size)
        ]
        for target, size in zip(endpoints, (913, 1107))
    }
    expected = Counter(
        service for rows in records.values() for service, status in rows
        if 500 <= status <= 599
    )
    summarize = """
import csv
import json
from collections import Counter
from pathlib import Path

with Path("access.csv").open() as source:
    counts = Counter(row["service"] for row in csv.DictReader(source)
                     if 500 <= int(row["status"]) <= 599)
"""
    west = _python(summarize + "print(json.dumps(counts))\n")
    east = f"WEST_COUNTS={PREVIOUS_STDOUT} " + _python(summarize + """
import os
counts.update(json.loads(os.environ["WEST_COUNTS"]))
Path("stage-1.json").write_text(json.dumps(counts))
""")
    return RemoteCase(
        "cross-target-logs",
        {
            target: {"access.csv": _csv(("service", "status"), rows)}
            for target, rows in records.items()
        },
        (
            "Aggregate server errors across the east and west targets. Each has a separate "
            "/work/access.csv with service and HTTP status columns. Count rows with status "
            "500 through 599 inclusive, grouped by service across both targets. Write a JSON "
            "object mapping each service with at least one server error to its integer total "
            "in /work/stage-1.json on east. Preserve the input files.",
        ),
        ((("west", west), ("east", east)),),
        (dict(expected),),
    )


def _config():
    original = {"app": "catalog", "workers": 3, "queue_capacity": 24, "log_level": "info"}
    updated = {**original, "workers": 6, "queue_capacity": 36}
    validator = """
import json
from pathlib import Path

config = json.loads(Path("app.json").read_text())
workers = config["workers"]
capacity = config["queue_capacity"]
if type(workers) is not int or not 1 <= workers <= 12:
    raise SystemExit("workers must be an integer from 1 through 12")
if type(capacity) is not int or not workers * 4 <= capacity <= workers * 8:
    raise SystemExit("queue_capacity must be between 4 and 8 times workers")
print(json.dumps({"valid": True, "workers": workers, "queue_capacity": capacity}))
""".lstrip()
    edit = _python("""
import json
from pathlib import Path

path = Path("app.json")
config = json.loads(path.read_text())
config.update(workers=6, queue_capacity=36)
path.write_text(json.dumps(config, indent=2) + "\\n")
""")
    return RemoteCase(
        "config-and-validate",
        {
            "east": {},
            "west": {"app.json": json.dumps(original, indent=2) + "\n", "validate.py": validator},
        },
        (
            "Update /work/app.json on west to set workers to 6 and queue_capacity to 36. "
            "Preserve all other keys and values. Run python3 validate.py in /work on west "
            "and write its JSON stdout to /work/stage-1.json on east. Preserve validate.py.",
        ),
        ((
            ("west", edit),
            ("west", "python3 validate.py"),
            ("east", f"printf '%s\\n' {PREVIOUS_STDOUT} > stage-1.json"),
        ),),
        ({"valid": True, "workers": 6, "queue_capacity": 36},),
        (("west", "app.json", updated),),
    )


def _analytics():
    rng = random.Random(3816)
    groups = ("amber", "jade", "violet")
    rows = [(rng.choice(groups), rng.randrange(-30, 130)) for _ in range(12000)]
    stages, actions, expected = [], [], []
    for stage, (group, threshold) in enumerate(zip(groups, (25, 60, 95)), 1):
        context = (
            "Analyze /work/metrics.csv on east, whose columns are group and integer value. "
            if stage == 1 else "Continue analyzing the same dataset on east. "
        )
        stages.append(
            context + f"Select {group} rows with value strictly above {threshold}. "
            f"Write a JSON object with integer values under the keys count and sum in "
            f"/work/stage-{stage}.json on east. Preserve the input dataset."
        )
        selected = [value for label, value in rows if label == group and value > threshold]
        expected.append({"count": len(selected), "sum": sum(selected)})
        actions.append((("east", _python(f"""
import csv
import json
from pathlib import Path

count, total = 0, 0
with Path("metrics.csv").open() as source:
    for row in csv.DictReader(source):
        value = int(row["value"])
        if row["group"] == {group!r} and value > {threshold}:
            count += 1
            total += value
Path("stage-{stage}.json").write_text(json.dumps(dict(count=count, sum=total)))
""")),))
    return RemoteCase(
        "analytics-follow-ups",
        {"east": {"metrics.csv": _csv(("group", "value"), rows)}, "west": {}},
        tuple(stages),
        tuple(actions),
        tuple(expected),
    )


CASES = (_logs(), _config(), _analytics())


def install(case, workspaces):
    """Populate separate target workspaces with their own fixtures."""
    for target, fixtures in case.fixtures.items():
        root = Path(workspaces[target])
        root.mkdir(parents=True, exist_ok=True)
        for name, body in fixtures.items():
            (root / name).write_text(body, encoding="utf-8")


def _matches_json(path, expected):
    try:
        result = json.loads(read_artifact(path))
        return json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True)
    except (OSError, ValueError):
        return False


def verify(case, workspaces):
    """Check required outputs and fixture preservation from outside the model tools."""
    checks = {
        f"stage_{stage}": _matches_json(Path(workspaces["east"]) / f"stage-{stage}.json", expected)
        for stage, expected in enumerate(case.expected, 1)
    }
    edited = {(target, name): expected for target, name, expected in case.edited}
    for target, fixtures in case.fixtures.items():
        for name, body in fixtures.items():
            path = Path(workspaces[target]) / name
            if (target, name) in edited:
                checks[f"edit_{target}_{name}"] = _matches_json(path, edited[target, name])
            else:
                try:
                    intact = read_artifact(path) == body.encode("utf-8")
                except (OSError, ValueError):
                    intact = False
                checks[f"preserve_{target}_{name}"] = intact
    return checks
