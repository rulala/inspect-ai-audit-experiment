"""How many CORE-bench medium answers exist in the capsule before any code runs.

Simulates the medium scrub (rm -rf results), then searches every text file for each
question's gold values. String golds (labels/names) and numeric golds reported
separately: a numeric hit may be raw data rather than an exposed answer.
"""

import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

CACHE = Path.home() / "Library/Caches/inspect_evals/CORE-Bench/data"

def golds(record):
    out = []
    for d in record["results"]:
        for q, v in d.items():
            out.append((q, str(v)))
    # dedupe on question
    return {q: v for q, v in out}

rows = []
for name in ("core_test.json", "core_train.json"):
    for rec in json.load(open(CACHE / name)):
        cid = rec["capsule_id"]
        tar = CACHE / f"{cid}.tar.gz"
        if not tar.exists():
            continue
        with tempfile.TemporaryDirectory() as td:
            try:
                with tarfile.open(tar) as tf:
                    tf.extractall(td, filter="data")
            except Exception as ex:
                print(f"{cid}: extract failed {ex}", file=sys.stderr)
                continue
            root = next(Path(td).iterdir())
            shutil.rmtree(root / "results", ignore_errors=True)  # the medium scrub
            for q, v in golds(rec).items():
                if len(v) < 4:
                    continue
                numeric = bool(re.fullmatch(r"-?\d+(\.\d+)?", v))
                # word-boundary match; capture the matched line so every hit is
                # inspectable, and flag generic single-token golds
                generic = bool(re.fullmatch(r"[a-z]+", v)) and len(v) < 8
                r = subprocess.run(
                    ["grep", "-rInwF", "--exclude=*.eval", v, str(root)],
                    capture_output=True, text=True,
                )
                lines = [ln for ln in r.stdout.splitlines() if ln]
                files = {ln.split(":", 1)[0] for ln in lines}
                rows.append(dict(capsule=cid, split=name, numeric=numeric,
                                 generic=generic, question=q[:60], gold=v[:30],
                                 hits=len(files),
                                 example=lines[0].split(str(root))[-1][:160] if lines else ""))
        print(f"{cid} done", file=sys.stderr)

import csv
with open("dev/audit-logs/answer_census.csv", "w") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

import collections
by_type = collections.defaultdict(lambda: [0, 0])
per_item = collections.defaultdict(lambda: [0, 0])
for r in rows:
    k = "numeric" if r["numeric"] else ("string-generic" if r["generic"] else "string")
    by_type[k][0] += 1
    by_type[k][1] += r["hits"] > 0
    per_item[r["capsule"]][0] += 1
    per_item[r["capsule"]][1] += r["hits"] > 0
print("\nquestions with gold present pre-execution (after medium scrub):")
for k, (n, h) in by_type.items():
    print(f"  {k:8s} {h}/{n} = {h/n:.0%}")
items_any = sum(1 for n, h in per_item.values() if h > 0)
items_all = sum(1 for n, h in per_item.values() if h == n)
print(f"items with >=1 answer present: {items_any}/{len(per_item)}")
print(f"items with ALL answers present: {items_all}/{len(per_item)}")
