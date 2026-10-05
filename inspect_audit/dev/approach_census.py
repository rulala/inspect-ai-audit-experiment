"""How agents approach CORE-bench medium: route classification per attempt."""

import re
from pathlib import Path

import pandas as pd
from inspect_ai.log import read_eval_log, read_eval_log_samples

BASE = Path("reference/abc-scout-scanners/core_bench/eval-logs")
RUN = re.compile(r"\b(python3?|Rscript|jupyter|nbconvert|bash \S+\.sh|\./run)\b")
FAIL = re.compile(
    r"ModuleNotFoundError|No module named|command not found|there is no package"
    r"|Error in library|not installed", re.I)
READ = re.compile(r"sed -n|cat |head |grep |less |pdftotext|strings ")

rows = []
for p in BASE.rglob("*.eval"):
    if "synth" in p.parts: continue
    if read_eval_log(str(p), header_only=True).eval.task_args.get("difficulty") != "medium": continue
    for s in read_eval_log_samples(str(p), all_samples_required=False):
        calls, reads, ran, docker = [], 0, False, False
        for m in s.messages:
            for tc in (getattr(m, "tool_calls", None) or []):
                a = str(tc.arguments)
                calls.append((tc.function, a))
                if re.search(r"docker (build|run|compose)", a): docker = True
                if tc.function == "bash" and RUN.search(a): ran = True
                if tc.function == "bash" and READ.search(a): reads += 1
        env_fail = any(FAIL.search(str(m.text or "")) for m in s.messages if m.role == "tool")
        if docker: route = "docker (intended path)"
        elif ran and not env_fail: route = "direct run, env worked"
        elif env_fail and reads >= 5: route = "env failed -> mined artefacts"
        elif env_fail: route = "env failed -> thin (guess/give up)"
        else: route = "never executed"
        score = list(s.scores.values())[0].value if s.scores else "?"
        rows.append(dict(route=route, score=score))

d = pd.DataFrame(rows)
d.to_csv("dev/audit-logs/approach_census.csv", index=False)
t = d.groupby("route").agg(n=("score","size"), C_rate=("score", lambda x: (x=="C").mean().round(2)))
print(t.sort_values("n", ascending=False).to_string())
print("\ntotal:", len(d))
