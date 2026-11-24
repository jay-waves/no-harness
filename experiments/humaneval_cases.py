"""Artifact checks using OpenAI's MIT-licensed HumanEval tasks 0 and 1."""

import gzip
import hashlib
import json
from functools import lru_cache
from urllib.request import urlopen

from task_cases import Case

REVISION = "b9453e8f1865df0a0057a1383767caa400acfbcf"
DATA_URL = f"https://raw.githubusercontent.com/openai/human-eval/{REVISION}/data/HumanEval.jsonl.gz"
DATA_SHA256 = "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef"


@lru_cache(maxsize=1)
def read_tasks():
    with urlopen(DATA_URL, timeout=30) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != DATA_SHA256:
        raise ValueError("unexpected HumanEval dataset checksum")
    tasks = (json.loads(line) for line in gzip.decompress(data).splitlines())
    return {task["task_id"]: task for task in tasks if task["task_id"] in {"HumanEval/0", "HumanEval/1"}}


def make_humaneval_case(variant):
    if variant == 1:
        cases = (
            (([1.0, 2.0, 3.0], 0.5), False),
            (([-4.0, 1.0, -4.0], 0.25), True),
            (([0.0, 0.5, 3.0], 0.5), False),
        )
    elif variant == 2:
        cases = (
            (("( ) (( )) (( )( ))",), ["()", "(())", "(()())"]),
            ((" ((( ))) (())  ",), ["((()))", "(())"]),
            ((" ( ( ( ) ) ( ) ) ",), ["((())())"]),
        )
    else:
        raise ValueError(f"unknown HumanEval variant: {variant}")

    task = read_tasks()[f"HumanEval/{variant - 1}"]
    entry = task["entry_point"]
    source = task["prompt"] + task["canonical_solution"]
    prompts, actions, expected = [], [], []
    for stage, (args, answer) in enumerate(cases, 1):
        invocation = f"{entry}(" + ", ".join(map(repr, args)) + ")"
        prompt = f"Call {invocation} using solution.py and save the returned value as JSON."
        if stage == 1:
            prompt = (
                "Complete this function in solution.py while preserving its name and signature.\n"
                f"```python\n{task['prompt']}```\n{prompt}"
            )
        else:
            prompt += " Keep the existing solution and earlier answer files intact."
        prompts.append(prompt)
        write = f"Path('solution.py').write_text({source!r}, encoding='utf-8')\n" if stage == 1 else ""
        actions.append(
            write + f"from solution import {entry}\nvalue = {invocation}\ndetails = []\n"
        )
        expected.append(answer)

    hidden_code = f"from solution import {entry}\n{task['test']}\ncheck({entry})\n"
    return Case("humaneval", variant, {}, tuple(prompts), "", tuple(actions), tuple(expected), hidden_code=hidden_code)
