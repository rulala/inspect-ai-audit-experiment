"""BixBench v3 audit result + corroboration against benchmark model scoring.

Joins the per-sample audit verdicts (bixbench-audit-v3) to the actual attempt
scores from Laurence's BixBench run, so each audit grade can be checked against
how the benchmark model really performed. The finding: audit grades predict
model accuracy monotonically -- INCORRECT gold -> ~4% model accuracy, clean
CORRECT/SPECIFIED -> ~86% -- independent evidence the verdicts track real defects
rather than being plausible-sounding hallucinations.

Logs (re-fetch with `hawk download <id> -o <dir>`):
  audit   : bixbench-audit-v3-9n5jhqgsz50h96rz         (luna/high, 4 items, 205 samples)
  attempts: imported-imported-bixbenc-wiepam15yi6bi450 (gpt-5.6-sol, sandbox_mode=docker)

Usage: python dev/bixbench_v3_corroboration.py <audit.eval> <attempts.eval> [out_dir]
Writes: <out>/bixbench_v3_samples.csv, _tally.csv, _corroboration.csv
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

from inspect_ai.log import read_eval_log

ITEMS = ["gold-answer", "insufficiently-specified", "answer-format", "red-teaming"]


def model_accuracy(attempts_log: str) -> dict[str, float]:
    """One accuracy in [0,1] per sample id, from the benchmark's own scorer."""
    acc: dict[str, float] = {}
    for s in read_eval_log(attempts_log).samples or []:
        for sc in (s.scores or {}).values():
            v = sc.value
            if isinstance(v, dict) and "accuracy" in v:
                acc[str(s.id)] = float(v["accuracy"])
            elif isinstance(v, (int, float)):
                acc[str(s.id)] = float(v)
            elif str(v) in ("C", "1"):
                acc[str(s.id)] = 1.0
            elif str(v) in ("I", "0"):
                acc[str(s.id)] = 0.0
    return acc


def audit_grades(audit_log: str) -> dict[str, dict[str, str]]:
    """Per sample id, the grade recorded for each audit item."""
    out: dict[str, dict[str, str]] = {}
    for s in read_eval_log(audit_log).samples or []:
        out[str(s.id)] = {item: str(sc.value) for item, sc in (s.scores or {}).items()}
    return out


def main() -> None:
    audit_log, attempts_log = sys.argv[1], sys.argv[2]
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("out")
    out.mkdir(parents=True, exist_ok=True)

    acc = model_accuracy(attempts_log)
    grades = audit_grades(audit_log)

    # per-sample joined table
    with (out / "bixbench_v3_samples.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_id", "model_accuracy", *ITEMS])
        for sid in sorted(grades):
            g = grades[sid]
            w.writerow([sid, acc.get(sid, ""), *(g.get(i, "") for i in ITEMS)])

    # grade tally per item
    tally: dict[str, dict[str, int]] = {i: defaultdict(int) for i in ITEMS}
    for g in grades.values():
        for item, grade in g.items():
            tally[item][grade] += 1
    with (out / "bixbench_v3_tally.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "grade", "n", "pct"])
        for item in ITEMS:
            total = sum(tally[item].values()) or 1
            for grade, n in sorted(tally[item].items(), key=lambda kv: -kv[1]):
                w.writerow([item, grade, n, round(100 * n / total, 1)])

    # corroboration: model accuracy by audit grade
    bucket: dict[tuple[str, str], list[float]] = defaultdict(list)
    for sid, g in grades.items():
        if sid not in acc:
            continue
        for item, grade in g.items():
            bucket[(item, grade)].append(acc[sid])
    with (out / "bixbench_v3_corroboration.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "grade", "n", "model_accuracy_pct"])
        for item in ITEMS:
            rows = [(gr, v) for (it, gr), v in bucket.items() if it == item]
            for grade, v in sorted(rows, key=lambda r: -mean(r[1])):
                w.writerow([item, grade, len(v), round(100 * mean(v), 1)])

    overall = 100 * mean(acc.values()) if acc else 0.0
    print(f"samples: {len(grades)} audited, {len(acc)} model-scored")
    print(f"overall model accuracy: {overall:.1f}%")
    print(f"wrote 3 CSVs to {out}/")


if __name__ == "__main__":
    main()
