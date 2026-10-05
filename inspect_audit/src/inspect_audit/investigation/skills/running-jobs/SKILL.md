---
name: running-jobs
description: Submit, monitor and collect benchmark or per-item audit jobs through Hawk within the investigation allowance. Read before remote work.
---

## Choose the work

The seed's remote block identifies allowed packages, workers and log sources. Use
cheap allowed workers for plumbing/screens and stronger ones for difficult verification.
A benchmark job generates attempts; inspect_audit/audit investigates dataset items
with selected checks and optional recorded attempts. Choose the actual candidate and
judge explicitly, including task-specific judge arguments. model_roles alone does not
configure a judge that the task constructs independently.

Start from /workspace/jobs/templates/benchmark.yaml or audit.yaml when present. Copy,
set task arguments and sample selection, then submit the path with hawk_submit. Do not
invent an eval_set_id: submission assigns one. The host validates the config with
Hawk's schema and this investigation's allowlists. Fix a rejected config rather than
switching models to work around a deterministic error.

## Sources

Local /inputs/logs stay local. Remote jobs need an imported Hawk source from
remote.supplied_logs, or hawk:<eval set id> for a job you launched. Check
seed.evidence_access before relying on supplied logs. An S3 prefix is not proof of
indexing or access. Do not repeatedly fetch a source whose access probe failed.
Use logs() for remote population tables and selected transcripts; do not download an
entire corpus simply to count it. Adding historical logs to a child audit may still
require substantial fetching/staging. Verify dataset identity before joining attempts.

## Smoke, inspect, expand

Prove the intended configuration on one or two samples with retries off. Verify the
candidate, tools, scorer and outputs all worked before scaling. For healthy work,
inspect startup once and use jobs(action="wait"), which waits without model tokens.
Use watch for progress, logs for runner errors, and trace/stacktrace for stuck work.
The view-results and debug-stuck-eval references explain diagnostics if needed.

jobs(action="samples") lists outcomes; transcripts writes selected evidence under
/inputs/jobs/<label>/transcripts/. collect downloads logs and settles measured spending.
Keep your configs in /workspace/jobs. The ledger, not the existence of a YAML file,
is the record of what ran. Use list to find your existing jobs before launching more.
A resumed investigation reconciles pending submissions to avoid duplicate launches.

## Limits and failures

budget() reports local spend and remote reservations. Reservations multiply planned
sample cost by size, models, epochs, retry attempts and a model-role buffer. Unknown
prices do not imply remaining allowance. Inspect's solver cost_limit does not cap
scoring: bound grader args.config.max_tokens and check observed usage before expanding.
Infrastructure and storage charges have a different accounting scope.

Use working_limit for active time and time_limit for wall time including waits. Hawk
controls max_samples centrally; it is not a job config setting. The submission tool
explains required limits and policy caps. Scale in bounded batches.

A missing credential, malformed request or broken audit image is an audit limitation.
Record what it prevented, repair once if feasible, and avoid retry loops. Attribute a
failure to the benchmark only after ruling out our configuration and reconstruction.
