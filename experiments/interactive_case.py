"""Gated cross-host telemetry audit and its matched upfront control."""

import json
import random
import re
import shlex
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from remote_cases import PREVIOUS_STDOUT, _csv, _python
from task_cases import read_artifact


@dataclass(frozen=True)
class Stage:
    name: str
    request: str
    checks: tuple[tuple[str, str, dict], ...]
    actions: tuple[tuple[str, str], ...]
    release: dict[str, dict[str, str]] = field(default_factory=dict)
    clarification: str = ""


@dataclass(frozen=True)
class InteractiveCase:
    name: str
    fixtures: dict[str, dict[str, str]]
    stages: tuple[Stage, ...]
    control_fixtures: dict[str, dict[str, str]]
    control_stages: tuple[Stage, ...]


def install_fixtures(fixtures, workspaces):
    """Install only fixtures released to the named target."""
    for target, entries in fixtures.items():
        root = Path(workspaces[target])
        root.mkdir(parents=True, exist_ok=True)
        for name, body in entries.items():
            with (root / name).open("x", encoding="utf-8") as destination:
                destination.write(body)


def _read(workspaces, target, name):
    try:
        return read_artifact(Path(workspaces[target]) / name)
    except (OSError, ValueError):
        return None


def verify_stage(stage, workspaces, visible_fixtures, snapshots, question=""):
    """Evaluate this milestone and preserve byte-for-byte snapshots of prior outputs."""
    question = question or ""
    checks = {
        f"preserve_{target}_{name}": _read(workspaces, target, name) == body.encode("utf-8")
        for target, entries in visible_fixtures.items() for name, body in entries.items()
    }
    checks.update({
        f"snapshot_{target}_{name}": _read(workspaces, target, name) == body
        for (target, name), body in snapshots.items()
    })
    if stage.clarification:
        checks["clarification"] = bool(
            re.search(r"\b(threshold|cut[- ]?off|minimum(?:_errors)?|how many|at least)\b",
                      question, re.IGNORECASE)
            and re.search(r"\?|\b(please|specify|provide|tell me)\b", question, re.IGNORECASE)
        )
    else:
        checks["no_clarification"] = not question.strip()
    new_snapshots = {}
    for target, name, expected in stage.checks:
        body = _read(workspaces, target, name)
        matches = False
        if body is not None:
            try:
                matches = json.dumps(json.loads(body), sort_keys=True) == json.dumps(expected, sort_keys=True)
            except (ValueError, RecursionError):
                pass
        checks[f"output_{target}_{name}"] = matches
        if matches:
            new_snapshots[target, name] = body
    if all(checks.values()):
        snapshots.update(new_snapshots)
    return checks


# Embed these local computations in the reference Bash actions. The expected
# values below come directly from the independently constructed fixture counts.
SCRIPT = """
import csv
import json
import os
from collections import Counter
from pathlib import Path

def counts(path, patch=None):
    with Path(path).open() as source:
        rows = {row["event_id"]: row for row in csv.DictReader(source)}
    if patch:
        with Path(patch).open() as source:
            for row in csv.DictReader(source):
                rows[row["event_id"]]["status"] = row["status"]
    return Counter(row["service"] for row in rows.values() if 500 <= int(row["status"]) <= 599)

def load(path):
    return json.loads(Path(path).read_text())

def write(path, data):
    Path(path).write_text(json.dumps(data) + "\\n")
"""


def _action(target, body, *, transfer=False):
    command = _python(SCRIPT + "\n" + body)
    if transfer:
        command = f"RECEIVED={PREVIOUS_STDOUT} {command}"
    return target, command


EAST_BEFORE = 'write("east-before.json", counts("east-events.csv"))'
GET_WEST_BEFORE = 'print(json.dumps(counts("west-events.csv")))'
RECEIVE_WEST = 'west = json.loads(os.environ["RECEIVED"])\n'
TOTALS_BEFORE = 'write("totals-before.json", Counter(load("east-before.json")) + Counter(west))'
ALERTS_BEFORE = """
write("alerts-before.json", {
    name: count for name, count in load("totals-before.json").items() if count >= 38
})
"""
WEST_AFTER = 'write("west-after.json", counts("west-events.csv", "west-corrections.csv"))'
GET_WEST_AFTER = 'print(json.dumps(load("west-after.json")))'
TOTALS_AFTER = 'write("totals-after.json", counts("east-events.csv") + Counter(west))'
GET_COMPARISON = 'print(json.dumps([load("alerts-before.json"), load("totals-after.json")]))'
ALERT_CHANGES = """
before, totals = json.loads(os.environ["RECEIVED"])
after = {name for name, count in totals.items() if count >= 38}
write("alert-changes.json", {"entered": sorted(after - before.keys()), "exited": sorted(before.keys() - after)})
"""


def _case():
    rng = random.Random(4510)
    east_counts = {"billing": 21, "catalog": 20, "search": 18}
    west_counts = {"billing": 19, "catalog": 18, "search": 18}
    west_after = {**west_counts, "billing": 16, "search": 22}
    totals_before = {"billing": 40, "catalog": 38, "search": 36}
    totals_after = {"billing": 37, "catalog": 38, "search": 40}

    def events(target, error_counts):
        rows = []
        for service, count in error_counts.items():
            statuses = [rng.choice((500, 502, 503, 599)) for _ in range(count)]
            statuses += rng.choices((200, 201, 204, 404, 429, 499), k=1200)
            for index, status in enumerate(statuses, 1):
                rows.append((f"{target}-{service}-{index:04}", service, status))
        rng.shuffle(rows)
        return _csv(("event_id", "service", "status"), rows)

    fixtures = {"east": {"east-events.csv": events("east", east_counts)}, "west": {}}
    west_release = {"west": {"west-events.csv": events("west", west_counts)}}
    correction_rows = [(f"west-billing-{index:04}", 200) for index in range(1, 4)]
    correction_rows += [(f"west-search-{index:04}", 503) for index in range(19, 23)]
    correction = {"west": {"west-corrections.csv": _csv(("event_id", "status"), correction_rows)}}
    stages = (
        Stage(
            "east-baseline",
            "Take a baseline snapshot from /work/east-events.csv on east. It contains one row per event "
            "with event_id, service, and integer status. Count server errors (HTTP 500 through 599 inclusive) "
            "by service and write /work/east-before.json on east. Write service counts as JSON objects "
            "mapping service names to integer counts. Keep source files and earlier artifacts intact.",
            (("east", "east-before.json", east_counts),),
            (_action("east", EAST_BEFORE),),
        ),
        Stage(
            "global-baseline",
            "Use the initial export /work/west-events.csv on west in the same format. "
            "Combine its server error counts with the saved east snapshot. Write the combined service counts "
            "to /work/totals-before.json on east.",
            (("east", "totals-before.json", totals_before),),
            (_action("west", GET_WEST_BEFORE), _action("east", RECEIVE_WEST + TOTALS_BEFORE, transfer=True)),
            release=west_release,
        ),
        Stage(
            "missing-threshold",
            "I also need the services with at least our minimum server error count, from the combined baseline. "
            "The minimum is a user-supplied parameter. If you need the parameter, ask via the question "
            "field and wait for my reply before doing any work for this request.",
            (),
            (),
            clarification="What minimum server error count should I use?",
        ),
        Stage(
            "baseline-alerts",
            "Use a minimum of 38 server errors, inclusive. Write the qualifying service counts from the combined "
            "baseline to /work/alerts-before.json on east.",
            (("east", "alerts-before.json", {"billing": 40, "catalog": 38}),),
            (_action("east", ALERTS_BEFORE),),
        ),
        Stage(
            "west-revision",
            "Use /work/west-corrections.csv on west. Each row gives an existing event_id and its "
            "replacement integer status. Apply the replacements in memory to west's original export before counting. "
            "Write the revised west-only server error counts to /work/west-after.json on west.",
            (("west", "west-after.json", west_after),),
            (_action("west", WEST_AFTER),),
            release=correction,
        ),
        Stage(
            "global-recheck",
            "Recheck the combined totals using the unchanged east export and the corrected west counts. Write the "
            "full revised service counts to /work/totals-after.json on east.",
            (("east", "totals-after.json", totals_after),),
            (_action("west", GET_WEST_AFTER), _action("east", RECEIVE_WEST + TOTALS_AFTER, transfer=True)),
        ),
        Stage(
            "alert-changes",
            "Compare baseline and revised alert membership at the same inclusive minimum of 38. On west write "
            "/work/alert-changes.json with keys entered (newly qualifying services) and exited (services that used "
            "to qualify), each an alphabetically sorted JSON array of service names.",
            (("west", "alert-changes.json", {"entered": ["search"], "exited": ["billing"]}),),
            (_action("east", GET_COMPARISON), _action("west", ALERT_CHANGES, transfer=True)),
        ),
    )
    output_stages = [stage for stage in stages if not stage.clarification]
    control = Stage(
        "upfront",
        "Both baseline exports and the west corrections are available now. Complete all of the following "
        "milestones, using the original west export for the baseline snapshots. "
        "The alert minimum of 38 has already been supplied.\n"
        + "\n".join(f"- {stage.request}" for stage in output_stages),
        tuple(check for stage in output_stages for check in stage.checks),
        (
            _action("west", WEST_AFTER + '\nprint(json.dumps([counts("west-events.csv"), load("west-after.json")]))'),
            _action("east", "\n".join((
                EAST_BEFORE,
                'west, corrected = json.loads(os.environ["RECEIVED"])',
                TOTALS_BEFORE,
                ALERTS_BEFORE,
                "west = corrected",
                TOTALS_AFTER,
                GET_COMPARISON,
            )), transfer=True),
            _action("west", ALERT_CHANGES, transfer=True),
        ),
    )
    return InteractiveCase(
        "telemetry-incident", fixtures, stages,
        {"east": fixtures["east"], "west": {**west_release["west"], **correction["west"]}},
        (control,),
    )


CASE = _case()


def _smoke(stages, fixtures):
    with tempfile.TemporaryDirectory(prefix="one-interactive-smoke-") as directory:
        workspaces = {target: Path(directory) / target for target in fixtures}
        visible = {target: files.copy() for target, files in fixtures.items()}
        install_fixtures(visible, workspaces)
        snapshots = {}
        for stage in stages:
            install_fixtures(stage.release, workspaces)
            for target, files in stage.release.items():
                visible[target].update(files)
            previous = ""
            for target, template in stage.actions:
                previous = subprocess.check_output(
                    ["bash", "-c", template.replace(PREVIOUS_STDOUT, shlex.quote(previous))],
                    cwd=workspaces[target], text=True,
                )
            checks = verify_stage(stage, workspaces, visible, snapshots, stage.clarification)
            if not all(checks.values()):
                raise RuntimeError(f"{stage.name}: {checks}")
        return {key: json.loads(value) for key, value in snapshots.items()}


if __name__ == "__main__":
    staged = _smoke(CASE.stages, CASE.fixtures)
    upfront = _smoke(CASE.control_stages, CASE.control_fixtures)
    if staged != upfront:
        raise SystemExit("staged and upfront artifacts differ")
    print(f"{len(CASE.stages)} milestones passed; {len(staged)} artifacts match the upfront control.")
