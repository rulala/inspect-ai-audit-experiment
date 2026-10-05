"""Run the CORE-bench ground-truth-access audit.

Usage: .venv/bin/python dev/run_corebench_audit.py <log_dir> <sample_id> [...]
"""

import sys
from pathlib import Path

from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelCost, set_model_cost

from inspect_audit import audit_task, resolve_task

MODEL = "openrouter/openai/gpt-5.6-luna"
# real medium-difficulty runs only: synth/ holds planted violations, and the
# easy-variant runs answer different instructions than the audited task
from inspect_ai.log import read_eval_log

LOGS = sorted(
    str(f)
    for f in Path("reference/abc-scout-scanners/core_bench/eval-logs").rglob("*.eval")
    if "synth" not in f.parts
    and read_eval_log(str(f), header_only=True).eval.task_args.get("difficulty") == "medium"
)

# openrouter pricing per 1M tokens, fetched 2026-08-18
set_model_cost(
    MODEL,
    ModelCost(
        input=0.20, output=1.20, input_cache_read=0.02, input_cache_write=0.25
    ),
)

log_dir, samples = sys.argv[1], sys.argv[2:]
if samples and samples[0] == "--train":
    samples = samples[1:]
    sys.path.insert(0, "dev")
    from corebench_train import core_bench_train

    task = core_bench_train()
    LOGS = [log for log in LOGS if "/train/" in log]
else:
    task = resolve_task("inspect_evals/core_bench", {"difficulty": "medium", "limit": 45})
    LOGS = [log for log in LOGS if "/train/" not in log]

logs = inspect_eval(
    audit_task(task, LOGS, samples=samples, items=["ground-truth-access"]),
    model=MODEL,
    log_dir=log_dir,
    fail_on_error=False,
    retry_on_error=2,
    token_limit=1_500_000,
    cost_limit=0.5,
    # two networks per sample against docker's ~31-subnet default pool
    max_samples=8,
    display="rich",
)
log = logs[0]
print("status:", log.status)
for s in log.samples or []:
    score = (s.scores or {}).get("ground-truth-access")
    cost = s.model_usage and sum(u.total_tokens for u in s.model_usage.values())
    print(s.id, "->", score.value if score else None, "| tokens:", cost)
