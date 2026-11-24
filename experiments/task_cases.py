"""Synthetic fixtures, reference replay actions and independent answer checks."""
import json
import random
import stat
from dataclasses import dataclass
from pathlib import Path

FAMILIES = ("inventory", "analysis", "repair", "reconcile")
GROUPS = ("amber", "jade", "violet")
BROKEN = "def normalize(value):\n    return value.strip().replace(' ', '-')\n"
FIX = "def normalize(value):\n    return '-'.join(value.casefold().split())\n"


@dataclass
class Case:
    family: str
    variant: int
    fixtures: dict
    prompts: tuple
    setup: str
    actions: tuple
    expected: tuple
    hidden: tuple = ()
    hidden_code: str = ""

    @property
    def name(self):
        return f"{self.family}-{self.variant}"


def make_case(family, variant):
    rng = random.Random(20261124 + variant)
    count = (24, 60, 120)[(variant - 1) % 3]
    fixtures, prompts, actions, expected = {}, [], [], []
    hidden = ()
    if family == "inventory":
        setup = ""
        for i, group in enumerate(GROUPS, 1):
            size = rng.randrange(120, 1600)
            filename = f"{group}.txt"
            fixtures[filename] = "a" * (size - 1) + "\n"
            prompts.append(f"Count bytes in {filename}.")
            actions.append(f"details = []\nvalue = Path({filename!r}).stat().st_size")
            expected.append(size)
    elif family == "analysis":
        rows = [(rng.choice(GROUPS), rng.randrange(-12, 80)) for _ in range(count)]
        fixtures["events.csv"] = "group,value\n" + "".join(f"{g},{n}\n" for g, n in rows)
        setup = """import csv
with Path('events.csv').open() as file:
    records = [(r['group'], int(r['value'])) for r in csv.DictReader(file)]
"""
        for group in GROUPS:
            threshold = rng.randrange(5, 45)
            prompts.append(f"For {group}, report the count and sum of values strictly above {threshold} as a JSON object with keys count and sum.")
            actions.append(f"""details = [f"{{g}} value={{v}}" for g, v in records if g == {group!r}]
selected = [v for g, v in records if g == {group!r} and v > {threshold}]
value = dict(count=len(selected), sum=sum(selected))
""")
            selected = [num for label, num in rows if label == group and num > threshold]
            expected.append({"count": len(selected), "sum": sum(selected)})
    elif family == "repair":
        fixtures["normalizer.py"] = BROKEN
        examples = [
            ["  One  TWO ", "mixed Case", "a\tb"],
            [" x\n Y  ", "Straße   Café", "Already-normal"],
            ["  ", "A\u2003B", f"tag {variant}"],
        ]
        setup = ""
        for i, values in enumerate(examples):
            job = (
                "Repair normalizer.py. normalize(value) must casefold and join whitespace-delimited words with one hyphen. "
                "Preserve existing hyphens and punctuation. "
                if i == 0 else ""
            )
            prompts.append(job + f"Return a JSON list of normalized forms, in order, for {json.dumps(values, ensure_ascii=False)}.")
            patch = f"Path('normalizer.py').write_text({FIX!r}, encoding='utf-8')\n" if i == 0 else ""
            actions.append(patch + f"""from normalizer import normalize
samples = {values!r}
value = list(map(normalize, samples))
details = [f"{{a!r}} -> {{b!r}}" for a, b in zip(samples, value)]
""")
            expected.append(["-".join(s.casefold().split()) for s in values])
        words = ("Straße", "MiXeD", "İ", "X-Y", "Z!", f"TAG{variant}")
        hidden = tuple(s for example in examples for s in example) + ("", "\t A   b ", "A\u2003B") + tuple(
            " " + rng.choice(words) + rng.choice(("  ", "\t", "\u2003", "\n")) + rng.choice(words) + " "
            for _ in range(12)
        )
    elif family == "reconcile":
        left = {i: rng.randrange(-10, 99) for i in range(count) if i % 5 != 0}
        right = {i: rng.randrange(-10, 99) for i in range(count) if i % 7 != 0}
        for name, records in (("east", left), ("west", right)):
            fixtures[f"{name}.tsv"] = "".join(f"{key}\t{value}\n" for key, value in records.items())
        setup = """def read_ledger(path):
    return {int(k): int(v) for k, v in (line.split() for line in Path(path).read_text().splitlines())}
east = read_ledger('east.tsv')
west = read_ledger('west.tsv')
"""
        shared = sorted(set(left).intersection(right))
        for req, action, answer in (
            ("Return east-only IDs sorted numerically as a list.", "sorted(east.keys() - west.keys())", sorted(set(left) - set(right))),
            ("Return west-only IDs sorted numerically as a list.", "sorted(west.keys() - east.keys())", sorted(set(right) - set(left))),
            ("Return the sum of absolute east/west deltas for the shared IDs.", "sum(abs(east[i] - west[i]) for i in east.keys() & west.keys())", sum(abs(left[i] - right[i]) for i in shared)),
        ):
            prompts.append("Use the headerless east.tsv and west.tsv files, each with integer ID and value columns. " + req)
            actions.append(f"""details = [f"id={{i}} east={{east.get(i)}} west={{west.get(i)}}" for i in sorted(east.keys() | west.keys())]
value = {action}
""")
            expected.append(answer)
    else:
        raise ValueError(f"unknown family: {family}")
    return Case(family, variant, fixtures, tuple(prompts), setup, tuple(actions), tuple(expected), hidden)


def replay_code(case, stage, mode):
    prelude = "import json\nfrom pathlib import Path\n"
    if mode == "persistent":
        setup = case.setup if stage == 1 else ""
    else:
        setup = case.setup
    return prelude + setup + case.actions[stage - 1] + f"""
Path('stage-{stage}.json').write_text(json.dumps(value), encoding='utf-8')
print(json.dumps({{'stage': {stage}, 'answer': value}}))
for detail in details:
    print(detail)
"""


def install(case, workspace):
    for name, body in case.fixtures.items():
        (workspace / name).write_text(body, encoding="utf-8")


def verify(case, workspace, hidden_check):
    """Judge artifacts outside the model-visible tools; validate unchanged inputs too."""
    checks = {}
    for stage, expected in enumerate(case.expected, 1):
        try:
            answer = json.loads(read_artifact(workspace / f"stage-{stage}.json"))
            checks[f"stage_{stage}"] = json.dumps(answer, sort_keys=True) == json.dumps(expected, sort_keys=True)
        except (OSError, ValueError):
            checks[f"stage_{stage}"] = False
    for name, body in case.fixtures.items():
        if case.family == "repair" and name == "normalizer.py":
            continue
        try:
            checks[f"preserve_{name}"] = read_artifact(workspace / name) == body.encode("utf-8")
        except (OSError, ValueError):
            checks[f"preserve_{name}"] = False
    if case.hidden:
        expected = ["-".join(s.casefold().split()) for s in case.hidden]
        checks["hidden_regression"] = hidden_check(
            f"from normalizer import normalize\nassert [normalize(x) for x in {case.hidden!r}] == {expected!r}"
        )
    if case.hidden_code:
        checks["published_tests"] = hidden_check(case.hidden_code)
    return checks


def read_artifact(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
        raise ValueError(f"invalid benchmark artifact: {path.name}")
    return path.read_bytes()
