"""DataBench tables and answer keys from a pinned public release."""

import ast
import csv
from functools import lru_cache
from io import BytesIO, StringIO
from urllib.request import urlopen

from task_cases import Case

DATA_ROOT = (
    "https://huggingface.co/datasets/cardiffnlp/databench/resolve/"
    "e75d53add267d2f9cfa32efd65ad77f0807adfad/data"
)


def read_parquet(dataset, filename, columns):
    from pyarrow.parquet import read_table

    with urlopen(f"{DATA_ROOT}/{dataset}/{filename}.parquet", timeout=30) as response:
        return read_table(BytesIO(response.read()), columns=columns).to_pylist()


@lru_cache(maxsize=2)
def make_databench_case(variant):
    if variant == 1:
        dataset = "001_Forbes"
        filename = "forbes.csv"
        columns = ["category", "finalWorth"]
        steps = (
            (6, "r['category'] == 'Technology'", "len(selected)"),
            (22, "r['category'] == 'Technology'", "sorted(worths, reverse=True)[:6]"),
            (7, "r['category'] == 'Automotive'", "sum(worths)"),
        )
        indices = tuple(step[0] for step in steps)
        actions = tuple(
            f"selected = [r for r in records if {condition}]\n"
            "worths = [int(r['finalWorth']) for r in selected]\n"
            "details = selected[:128]\n"
            f"value = {answer}\n"
            for _, condition, answer in steps
        )
    elif variant == 2:
        dataset = "002_Titanic"
        filename = "titanic.csv"
        columns = ["Pclass", "Age", "Survived"]
        indices = (4, 0, 12)
        actions = (
            """details = records[:128]
value = len({int(r['Pclass']) for r in records})
""",
            """children = [r for r in records if r['Age'] and float(r['Age']) < 18]
details = children[:128]
value = any(r['Survived'] == 'True' for r in children)
""",
            """from collections import Counter
classes = Counter(int(r['Pclass']) for r in records)
survivors = Counter(int(r['Pclass']) for r in records if r['Survived'] == 'True')
details = records[:128]
value = sorted(classes, key=lambda c: survivors[c] / classes[c], reverse=True)
""",
        )
    else:
        raise ValueError(f"unknown DataBench variant: {variant}")

    qa = read_parquet(dataset, "qa", ["question", "answer"])
    prompts = [qa[i]["question"] for i in indices]
    prompts[0] = f"Use the table in {filename} for this session. {prompts[0]}"
    expected = tuple(ast.literal_eval(qa[i]["answer"]) for i in indices)

    fixture = StringIO()
    writer = csv.DictWriter(fixture, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(read_parquet(dataset, "all", columns))
    setup = f"""import csv
with Path({filename!r}).open(encoding='utf-8', newline='') as file:
    records = list(csv.DictReader(file))
"""
    return Case("databench", variant, {filename: fixture.getvalue()}, tuple(prompts), setup, actions, expected)
