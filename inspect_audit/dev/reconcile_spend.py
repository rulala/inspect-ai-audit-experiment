"""Exact token/cost reconciliation across today's luna audit runs.

Sums per-sample model_usage (log.stats.model_usage is broken for react-agent
tasks) across every downloaded .eval, prices at luna's OpenRouter rates, and
totals. The >272K-token-per-request override cannot be applied from aggregate
counts, so this is a base-rate lower bound.

Usage: python dev/reconcile_spend.py <dir-of-eval-logs>...
"""

import glob
import sys

from inspect_ai.log import read_eval_log

# luna OpenRouter base rates, $ per token
RATE = {
    "input_tokens": 0.0000002,
    "output_tokens": 0.0000012,
    "input_tokens_cache_read": 0.00000002,
    "input_tokens_cache_write": 0.00000025,
    "reasoning_tokens": 0.0000012,  # reasoning billed as output
}


def main() -> None:
    dirs = sys.argv[1:]
    grand: dict[str, int] = {}
    grand_cost = 0.0
    print(f"{'run':40s} {'in':>10s} {'out':>10s} {'cache_rd':>12s} {'cost$':>8s}")
    for d in dirs:
        for f in sorted(glob.glob(f"{d}/*.eval")):
            log = read_eval_log(f)
            agg: dict[str, int] = {}
            for s in log.samples or []:
                for _, u in (s.model_usage or {}).items():
                    for field, val in u.model_dump().items():
                        if isinstance(val, int):
                            agg[field] = agg.get(field, 0) + val
            if not agg:
                continue
            cost = sum(agg.get(k, 0) * r for k, r in RATE.items())
            grand_cost += cost
            for k, v in agg.items():
                grand[k] = grand.get(k, 0) + v
            name = f.split("/")[-2] + "/" + f.split("/")[-1][:18]
            print(
                f"{name:40s} {agg.get('input_tokens', 0):>10,} "
                f"{agg.get('output_tokens', 0):>10,} "
                f"{agg.get('input_tokens_cache_read', 0):>12,} {cost:>8.3f}"
            )
    print("\n=== GRAND TOTAL (all luna runs) ===")
    for k, v in sorted(grand.items()):
        print(f"  {k}: {v:,}")
    print(f"\n  TOTAL COST at base rates: ${grand_cost:.3f}")
    print("  (lower bound: >272K-token requests bill at 2x, not applied here)")


if __name__ == "__main__":
    main()
