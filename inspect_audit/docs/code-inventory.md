# Code inventory · 10 September 2026

This pass inspected package code, tests, development scripts and documentation.
It did not read AgentHarm reports or agent transcripts. Line counts below are physical
lines including comments and blanks, not executable statements. Lockfiles, logs and
output artifacts are excluded from the named categories.

| Category | Before | After |
| --- | ---: | ---: |
| Production Python (`src`, excluding skill helper scripts) | 7,869 | 7,822 |
| Python tests | 6,005 | 6,013 |
| Markdown prompts and skills under `src` | 3,772 | 3,572 |
| Optional frontend, all tracked files | 785 | 785 |
| Development scripts/configs, all tracked files | 3,593 | 3,593 |

The small production reduction is deliberate: moving live code is not deletion.
The larger reduction is in the default instructions, while upstream skill copies
remain available as reference material.

## Changes made

- Deleted `logged_tool_names` and `_rendered_text`: no callers in package, tests,
  frontend or development scripts. Existing tool-discrepancy and report-lint paths
  use other implementations.
- Removed unused bucket/profile fields from `Remote`, their default bucket constant,
  environment lookup and constructor plumbing. Public `investigate(log_bucket=...,
  aws_profile=...)` parameters remain accepted as documented compatibility no-ops.
- Moved the earlier conversational task to `_legacy_report.py`. The registry name
  `inspect_audit/report` remains available for the documented frontend workflow.
  `_report.py` now contains publication/validation and shared operator turn-taking.
- Reduced the system prompt to remit, stages and boundaries. Scientific method lives
  in `investigating`, remote execution in `running-jobs`, and presentation in `writing`.
- Removed four overlapping workflows from the default mounted skills, retaining their
  source copies: check-trajectories-workflow, eval-report-workflow, read-eval-logs,
  babysit-eval. The default now has eleven skills including the three support skills,
  down from fourteen. Skills are loaded on demand, not all inserted into the prompt.
- Replaced the one-line README with entry points, setup links and explicit legacy status.

## Custom machinery worth scrutinising

| Area | Why retained | Next useful simplification |
| --- | --- | --- |
| `_sandbox.py` (926 lines) | Merges benchmark/auditor services, restores state, and emits remote sandbox configuration; exercised by integration tests. | Highest integration risk: several private Inspect Docker/context imports. Establish equivalent supported lifecycle hooks before replacing it. |
| `_state.py` (596 lines) | Separates the tested attempt from the auditor conversation and mirrors benchmark tools. | Keep this measurement boundary; reduce adapters only where Inspect can preserve identical semantics. |
| `_contract.py` | Reconstructs registry-declared tools and reports incomplete recovery. | Dynamic tools remain a limitation. Prefer supported introspection when available; do not infer absence from failed reconstruction. |
| `_concordance.py` (488 lines) | Tests recorded-versus-replayed scoring agreement. | Keep while replay exists; agreement does not validate the benchmark itself. |
| `_jobs.py` (807 lines) | Hawk CLI wrapper, policy, ledger and reservations; config parsing already uses Hawk types. | Separate general transport from audit-specific restrictions if reuse warrants it. Do not recreate Hawk scheduling or auth. |
| `_investigate.py` (~1,900 lines) | Workspace preparation, remote tools, cost accounting, task assembly and rendering. | Largest cohesion problem. Extract workspace preparation and dispatch in focused follow-ups with existing tests as boundaries. |
| `_registry.py` log fetching | Remote manifests/Hawk logs are materialized for item auditing. | A shared lazy log source is desirable, but requires real log-access tests; deleting fetching would break current consumers. |
| Report components and IRT | Small display helpers around plotting/Graphviz; IRT delegates fitting to GIRTH. | Optional features, not an orchestration layer. Do not replace a statistics dependency with a homemade estimator merely to reduce dependencies. |

Inspect already supplies the task/agent loop, compaction, sandbox interface, logs,
scoring, ACP and model providers. The main custom burden is adapting benchmark state
and services for auditing, plus managing investigator-owned Hawk jobs. Several modules
import private Inspect APIs, so version upgrades need behavioural checks.

## Older does not establish unused

The report task and frontend have an explicit documented caller relationship. The
SWE-bench replay entry point has tests and a development extraction workflow. Developer
scripts contain benchmark-specific experiments and hard-coded historical settings;
these are outside the installed package and should be labelled/archived with their
owners, not silently deleted based on static import counts. Registry discovery and
external Python callers mean a text search cannot prove a public entry point unused.

## Deferred

No model changes or new audit runs. No rewriting of the report template/components in
this pass: a useful-output contract is the next milestone. No migration onto Hawk or
new identity/isolation scheme. The untracked HTML walkthrough from the previous task
is left untouched. Historical LOG.md and reviews are not edited.

Validation: existing non-Docker suite, lint/types, a compatibility check constructing
the legacy registry task, and the existing real-Docker investigation tests. Prompt
simplification is not evidence of improved audit quality; that needs a controlled run.
