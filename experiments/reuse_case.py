"""Deterministic two-table fixture and exact answer keys for the reuse experiment."""
import csv
import hashlib
import json
import os
from pathlib import Path
import stat


ANSWER_LIMIT = 16384
SIZES_MIB = (8, 128)
REGIONS = ("central", "east", "north", "west")
QUERIES = (
    ("baseline", 'Return an object {"rows": int, "adjusted_sum": int} for all retained rows.'),
    ("flagged", 'Return an object {"rows": int, "adjusted_sum": int} for retained rows with flag == 1.'),
    ("regions", "Return an object mapping each region to the sum of adjusted values for its retained rows."),
    ("high_value", 'For adjusted values >= 25000, return {"rows": int, "adjusted_sum": int}.'),
    ("east_flagged", "Return an object mapping group ID strings to retained flagged row counts in the east region. Include groups with at least one such row."),
    ("group_max", "Return an object mapping each retained group ID string to its maximum adjusted value."),
    ("west_small", 'For west records with flag == 0 and adjusted < 10000, return {"rows": int, "adjusted_sum": int}.'),
    ("region_winners", "Return an object mapping each region to the smallest group ID (integer) among its groups tied for the highest total adjusted value."),
)
SCHEMA = """Read the two CSV tables in /work.
source.csv has no header. Its columns are id, group, value, flag (decimal integers).
groups.csv has a header group,region,multiplier,active. Group, multiplier and active are integers.
Join on group. Retain only rows whose value is nonzero and whose group has active == 1.
For each retained row, adjusted = value * multiplier. Group IDs in JSON object keys are decimal strings.
"""


def seed(workspace: Path, size_mib: int):
    """Write the same source block repeatedly and derive answers before repetition."""
    dimensions = [(group, REGIONS[group % 4], 1 + group % 5, int(group % 7 != 0))
                  for group in range(64)]
    with (workspace / "groups.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("group", "region", "multiplier", "active"))
        writer.writerows(dimensions)

    block_rows = 4096
    row_bytes = 32
    block_count, remainder = divmod(size_mib * 1024**2, block_rows * row_bytes)
    if block_count <= 0 or remainder:
        raise ValueError("Fixture size must be a positive whole number of MiB")

    block_data = []
    joined = []
    for row in range(block_rows):
        group = (row * 17 + row // 64) % len(dimensions)
        value = 0 if row % 23 == 0 else 1 + (row * 7919) % 14407
        flag = (row ^ (row >> 5)) & 1
        block_data.append(f",{group:04d},{value:012d},{flag:02d}\n".encode())
        _, region, multiplier, active = dimensions[group]
        if value and active:
            joined.append((group, region, value * multiplier, flag))

    with (workspace / "source.csv").open("wb") as handle:
        for block in range(block_count):
            start = block * block_rows
            handle.write(b"".join(f"{start + offset:010d}".encode() + suffix
                                  for offset, suffix in enumerate(block_data)))

    def summary(rows):
        return {"rows": len(rows) * block_count,
                "adjusted_sum": sum(row[2] for row in rows) * block_count}

    totals = {group: sum(row[2] for row in joined if row[0] == group)
              for group, _, _, active in dimensions if active}
    east_flagged = {}
    for group, region, _, flag in joined:
        if region == "east" and flag == 1:
            east_flagged[str(group)] = east_flagged.get(str(group), 0) + block_count
    return {
        "baseline": summary(joined),
        "flagged": summary([row for row in joined if row[3] == 1]),
        "regions": {region: sum(row[2] for row in joined if row[1] == region) * block_count
                    for region in REGIONS},
        "high_value": summary([row for row in joined if row[2] >= 25000]),
        "east_flagged": east_flagged,
        "group_max": {str(group): max(row[2] for row in joined if row[0] == group)
                      for group in totals},
        "west_small": summary([row for row in joined
                               if row[1] == "west" and row[3] == 0 and row[2] < 10000]),
        "region_winners": {region: max((group for group, name, _, active in dimensions
                                        if active and name == region),
                                       key=lambda group: (totals[group], -group))
                           for region in REGIONS},
    }


def fingerprints(workspace):
    hashes = {}
    for name in ("source.csv", "groups.csv"):
        path = workspace / name
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            hashes[name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return hashes


def answer_matches(path, expected):
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > ANSWER_LIMIT:
                return False
            actual = json.loads(handle.read(ANSWER_LIMIT + 1))
            # Canonical JSON comparison distinguishes booleans and floats from integers.
            return json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
    except (OSError, ValueError, RecursionError):
        return False
