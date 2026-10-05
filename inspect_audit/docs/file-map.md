# Complete file map

142 files/entries: tracked files plus current cleanup additions. Excludes ignored virtual environments, caches and runtime logs. Historical artifacts are listed by path without reading their contents. AgentHarm outputs are outside this checkout and were not read.

## Repository, docs and build files

| File | Purpose |
| --- | --- |
| [.github/workflows/ci.yml](../.github/workflows/ci.yml) | Continuous-integration checks. |
| [.gitignore](../.gitignore) | Excludes environments, caches and local artifacts. |
| [AUDIT_CATALOGUE.md](../AUDIT_CATALOGUE.md) | Developer catalogue of audit work; historical/reference material. |
| [LOG.md](../LOG.md) | Historical development record; preserved, not edited in this cleanup. |
| [Makefile](../Makefile) | Install, lint/typecheck, unit-test and Docker-test commands. |
| [README.md](../README.md) | Package overview, supported entry points and setup links. |
| [docker/auditor/Dockerfile](../docker/auditor/Dockerfile) | Build recipe for the published remote auditor image. |
| [docker/auditor/README.md](../docker/auditor/README.md) | Instructions for building/publishing that image. |
| [docs/code-inventory.md](../docs/code-inventory.md) | Cleanup findings, LoC counts, remaining custom machinery and deferred work. |
| [docs/current-setup.html](../docs/current-setup.html) | Earlier standalone architecture walkthrough; not runtime code. |
| [docs/file-map.md](../docs/file-map.md) | This complete file-by-file inventory. |
| [docs/investigation.md](../docs/investigation.md) | Operator setup: inputs, Hawk, budgets, recovery and skill ownership. |
| [pyproject.toml](../pyproject.toml) | Package metadata, dependencies, Inspect entry point and tool configuration. |
| [uv.lock](../uv.lock) | Pinned dependency resolution. |

## Developer experiments and historical artifacts

| File | Purpose |
| --- | --- |
| [dev/answer_census.py](../dev/answer_census.py) | How many CORE-bench medium answers exist in the capsule before any code runs. |
| [dev/approach_census.py](../dev/approach_census.py) | How agents approach CORE-bench medium: route classification per attempt. |
| [dev/audit-logs/corrupt10-quoted_results.csv](../dev/audit-logs/corrupt10-quoted_results.csv) | Historical result table. Listed by filename only; not inspected for this inventory. |
| [dev/audit-logs/corrupt10-v2_results.csv](../dev/audit-logs/corrupt10-v2_results.csv) | Historical result table. Listed by filename only; not inspected for this inventory. |
| [dev/audit-logs/flash10_results.csv](../dev/audit-logs/flash10_results.csv) | Historical result table. Listed by filename only; not inspected for this inventory. |
| [dev/audit-logs/n200/.eval-set-id](../dev/audit-logs/n200/.eval-set-id) | Historical eval-set identifier. |
| [dev/audit-logs/n200/eval-set.json](../dev/audit-logs/n200/eval-set.json) | Historical eval-set state/config artifact. |
| [dev/audit-logs/n200/logs.json](../dev/audit-logs/n200/logs.json) | Historical log index. |
| [dev/audit-logs/n200_results.csv](../dev/audit-logs/n200_results.csv) | Historical result table. Listed by filename only; not inspected for this inventory. |
| [dev/audit-logs/n800/.eval-set-id](../dev/audit-logs/n800/.eval-set-id) | Historical eval-set identifier. |
| [dev/audit-logs/n800/eval-set.json](../dev/audit-logs/n800/eval-set.json) | Historical eval-set state/config artifact. |
| [dev/audit-logs/n800/logs.json](../dev/audit-logs/n800/logs.json) | Historical log index. |
| [dev/audit-logs/n800_results.csv](../dev/audit-logs/n800_results.csv) | Historical result table. Listed by filename only; not inspected for this inventory. |
| [dev/audit-logs/rerun38/.eval-set-id](../dev/audit-logs/rerun38/.eval-set-id) | Historical eval-set identifier. |
| [dev/audit-logs/rerun38/eval-set.json](../dev/audit-logs/rerun38/eval-set.json) | Historical eval-set state/config artifact. |
| [dev/audit-logs/rerun38/logs.json](../dev/audit-logs/rerun38/logs.json) | Historical log index. |
| [dev/audit-logs/rerun38_results.csv](../dev/audit-logs/rerun38_results.csv) | Historical result table. Listed by filename only; not inspected for this inventory. |
| [dev/audit-logs/scale10-v1/.eval-set-id](../dev/audit-logs/scale10-v1/.eval-set-id) | Historical eval-set identifier. |
| [dev/audit-logs/scale10-v1/eval-set.json](../dev/audit-logs/scale10-v1/eval-set.json) | Historical eval-set state/config artifact. |
| [dev/audit-logs/scale10-v1/logs.json](../dev/audit-logs/scale10-v1/logs.json) | Historical log index. |
| [dev/audit-logs/scale10-v2/.eval-set-id](../dev/audit-logs/scale10-v2/.eval-set-id) | Historical eval-set identifier. |
| [dev/audit-logs/scale10-v2/eval-set.json](../dev/audit-logs/scale10-v2/eval-set.json) | Historical eval-set state/config artifact. |
| [dev/audit-logs/scale10-v2/logs.json](../dev/audit-logs/scale10-v2/logs.json) | Historical log index. |
| [dev/audit-logs/scale10_results.csv](../dev/audit-logs/scale10_results.csv) | Historical result table. Listed by filename only; not inspected for this inventory. |
| [dev/audit-logs/sweep/.eval-set-id](../dev/audit-logs/sweep/.eval-set-id) | Historical eval-set identifier. |
| [dev/audit-logs/sweep/eval-set.json](../dev/audit-logs/sweep/eval-set.json) | Historical eval-set state/config artifact. |
| [dev/audit-logs/sweep/logs.json](../dev/audit-logs/sweep/logs.json) | Historical log index. |
| [dev/bixbench_v3_corroboration.py](../dev/bixbench_v3_corroboration.py) | BixBench v3 audit result + corroboration against benchmark model scoring. |
| [dev/corebench_train.py](../dev/corebench_train.py) | The CORE-bench train split, reconstructed. |
| [dev/extract_exploits.py](../dev/extract_exploits.py) | Extract replayable exploits from a red-teaming audit log. |
| [dev/logs](../dev/logs) | Tracked log-location entry; developer data plumbing, not production code. |
| [dev/reconcile_spend.py](../dev/reconcile_spend.py) | Exact token/cost reconciliation across today's luna audit runs. |
| [dev/run_corebench_audit.py](../dev/run_corebench_audit.py) | Run the CORE-bench ground-truth-access audit. |
| [dev/score_pilot.py](../dev/score_pilot.py) | Join a pilot audit log against the Mohl ground_truth_access labels. |

## Frontend

| File | Purpose |
| --- | --- |
| [frontend/README.md](../frontend/README.md) | How to run the optional ACP frontend. |
| [frontend/server.py](../frontend/server.py) | Optional loopback ACP WebSocket bridge and static-page server. |
| [frontend/static/excerpt-demo.html](../frontend/static/excerpt-demo.html) | Transcript presentation demo, not the primary chat page. |
| [frontend/static/index.html](../frontend/static/index.html) | Browser chat client for a running ACP session. |
| [frontend/static/ux-mock.html](../frontend/static/ux-mock.html) | UI mock-up, not the primary chat page. |

## Package code and assets

| File | Purpose |
| --- | --- |
| [src/inspect_audit/__init__.py](../src/inspect_audit/__init__.py) | Public Python exports and version. |
| [src/inspect_audit/_agent.py](../src/inspect_audit/_agent.py) | Per-item audit agent, tool grants, evidence/verdict validation, benchmark grading and image previews. |
| [src/inspect_audit/_audit.py](../src/inspect_audit/_audit.py) | Builds an Inspect audit task: selects items, groups recorded attempts and assembles per-item sessions. |
| [src/inspect_audit/_concordance.py](../src/inspect_audit/_concordance.py) | Replays/regrades attempts and classifies agreement with historical scores. |
| [src/inspect_audit/_contract.py](../src/inspect_audit/_contract.py) | Recovers registry-declared prompts/tools and reports discrepancies with observed tools. |
| [src/inspect_audit/_investigate.py](../src/inspect_audit/_investigate.py) | Outer investigation task; workspace preparation, costs, remote tools, log access and agent assembly. |
| [src/inspect_audit/_item.py](../src/inspect_audit/_item.py) | AuditItem/AttemptRef models; stages item source, metadata, media and sliced attempt logs. |
| [src/inspect_audit/_jobs.py](../src/inspect_audit/_jobs.py) | Hawk CLI/API adapter, job ledger, config policy, reservations, collection and cost accounting. |
| [src/inspect_audit/_legacy_report.py](../src/inspect_audit/_legacy_report.py) | Older conversational report agent and its log sandbox; retained for existing ACP clients. |
| [src/inspect_audit/_registry.py](../src/inspect_audit/_registry.py) | Registered task entry points, remote log fetching, probes and SWE-bench exploit replay. |
| [src/inspect_audit/_report.py](../src/inspect_audit/_report.py) | Finding schema, evidence-path validation, HTML checks, versioned publication and ACP turn-taking. |
| [src/inspect_audit/_resolve.py](../src/inspect_audit/_resolve.py) | Resolves an Inspect task from a registry/file name or recorded log configuration. |
| [src/inspect_audit/_sandbox.py](../src/inspect_audit/_sandbox.py) | Combines auditor and benchmark containers; restores benchmark state; emits Docker/Helm configuration. |
| [src/inspect_audit/_state.py](../src/inspect_audit/_state.py) | Reconstructed benchmark attempt state; edits, tool mirroring and separation from auditor messages. |
| [src/inspect_audit/containers/__init__.py](../src/inspect_audit/containers/__init__.py) | Loads Docker/Compose/Helm text templates. |
| [src/inspect_audit/containers/auditor.Dockerfile](../src/inspect_audit/containers/auditor.Dockerfile) | Local auditor image template with task-package requirements. |
| [src/inspect_audit/containers/auditor.compose.yaml](../src/inspect_audit/containers/auditor.compose.yaml) | Generic local auditor service template. |
| [src/inspect_audit/containers/egress.helm.yaml](../src/inspect_audit/containers/egress.helm.yaml) | Remote auditor egress policy template. |
| [src/inspect_audit/investigation/Dockerfile](../src/inspect_audit/investigation/Dockerfile) | Outer investigator analysis image: Inspect, plotting, Quarto and SVG rendering. |
| [src/inspect_audit/investigation/report/components.py](../src/inspect_audit/investigation/report/components.py) | Shared plots, tables, transcript blocks and Graphviz benchmark diagrams, with saved data. |
| [src/inspect_audit/investigation/report/findings.json](../src/inspect_audit/investigation/report/findings.json) | Empty starting register, not investigation results. |
| [src/inspect_audit/investigation/report/findings.schema.json](../src/inspect_audit/investigation/report/findings.schema.json) | Agent-facing JSON schema for the findings register. |
| [src/inspect_audit/investigation/report/irt_analysis.py](../src/inspect_audit/investigation/report/irt_analysis.py) | Screens response matrices and delegates optional Rasch/2PL fitting to GIRTH. |
| [src/inspect_audit/investigation/report/report.qmd](../src/inspect_audit/investigation/report/report.qmd) | Starting Quarto report template. |
| [src/inspect_audit/investigation/report/styles.css](../src/inspect_audit/investigation/report/styles.css) | Shared report typography, colour and layout. |
| [src/inspect_audit/investigation/skills/VENDORED.md](../src/inspect_audit/investigation/skills/VENDORED.md) | Skill provenance, adaptation and default-loadout notes. |
| [src/inspect_audit/investigation/skills/babysit-eval/SKILL.md](../src/inspect_audit/investigation/skills/babysit-eval/SKILL.md) | Retained upstream workflow; no longer mounted by default. |
| [src/inspect_audit/investigation/skills/check-trajectories-workflow/SKILL.md](../src/inspect_audit/investigation/skills/check-trajectories-workflow/SKILL.md) | Retained upstream trajectory workflow; no longer mounted by default. |
| [src/inspect_audit/investigation/skills/debug-stuck-eval/SKILL.md](../src/inspect_audit/investigation/skills/debug-stuck-eval/SKILL.md) | Hawk diagnosis reference. |
| [src/inspect_audit/investigation/skills/eval-report-workflow/SKILL.md](../src/inspect_audit/investigation/skills/eval-report-workflow/SKILL.md) | Retained upstream report workflow; no longer mounted by default. |
| [src/inspect_audit/investigation/skills/eval-report-workflow/references/frontier-models.md](../src/inspect_audit/investigation/skills/eval-report-workflow/references/frontier-models.md) | Supporting reference for the adjacent skill. |
| [src/inspect_audit/investigation/skills/eval-validity-review/SKILL.md](../src/inspect_audit/investigation/skills/eval-validity-review/SKILL.md) | Focused reference checklist for measurement claims. |
| [src/inspect_audit/investigation/skills/investigate-dataset/SKILL.md](../src/inspect_audit/investigation/skills/investigate-dataset/SKILL.md) | Focused dataset-review reference. |
| [src/inspect_audit/investigation/skills/investigate-dataset/references/inspect-dataset-patterns.md](../src/inspect_audit/investigation/skills/investigate-dataset/references/inspect-dataset-patterns.md) | Supporting reference for the adjacent skill. |
| [src/inspect_audit/investigation/skills/investigating/SKILL.md](../src/inspect_audit/investigation/skills/investigating/SKILL.md) | Scientific investigation method and evidence discipline. |
| [src/inspect_audit/investigation/skills/investigating/examples/audit.eval-set.yaml](../src/inspect_audit/investigation/skills/investigating/examples/audit.eval-set.yaml) | Static example job config; generated policy-specific templates are preferred at runtime. |
| [src/inspect_audit/investigation/skills/investigating/examples/benchmark.eval-set.yaml](../src/inspect_audit/investigation/skills/investigating/examples/benchmark.eval-set.yaml) | Static example job config; generated policy-specific templates are preferred at runtime. |
| [src/inspect_audit/investigation/skills/read-eval-logs/SKILL.md](../src/inspect_audit/investigation/skills/read-eval-logs/SKILL.md) | Retained overlapping log workflow; no longer mounted by default. |
| [src/inspect_audit/investigation/skills/running-jobs/SKILL.md](../src/inspect_audit/investigation/skills/running-jobs/SKILL.md) | Hawk submission, smoke tests, monitoring, collection and budgets. |
| [src/inspect_audit/investigation/skills/security-audit-eval/SKILL.md](../src/inspect_audit/investigation/skills/security-audit-eval/SKILL.md) | Reference for benchmarks executing untrusted code. |
| [src/inspect_audit/investigation/skills/view-results/SKILL.md](../src/inspect_audit/investigation/skills/view-results/SKILL.md) | Hawk result-inspection reference. |
| [src/inspect_audit/investigation/skills/writing/SKILL.md](../src/inspect_audit/investigation/skills/writing/SKILL.md) | Report presentation and publication instructions. |
| [src/inspect_audit/prompts/__init__.py](../src/inspect_audit/prompts/__init__.py) | Loads prompt text constants. |
| [src/inspect_audit/prompts/audit.md](../src/inspect_audit/prompts/audit.md) | Per-item auditor system prompt. |
| [src/inspect_audit/prompts/audit_confidential.md](../src/inspect_audit/prompts/audit_confidential.md) | Conditional instructions for unpublished benchmark material. |
| [src/inspect_audit/prompts/audit_notes.md](../src/inspect_audit/prompts/audit_notes.md) | Operator-steer insertion template. |
| [src/inspect_audit/prompts/investigate.md](../src/inspect_audit/prompts/investigate.md) | Outer investigator remit, stages and boundaries. |
| [src/inspect_audit/prompts/report.md](../src/inspect_audit/prompts/report.md) | Legacy synthesis prompt with staged logs. |
| [src/inspect_audit/prompts/report_chat_only.md](../src/inspect_audit/prompts/report_chat_only.md) | Legacy no-log ACP chat prompt. |
| [src/inspect_audit/py.typed](../src/inspect_audit/py.typed) | Marks the package as providing type information. |
| [src/inspect_audit/report_skills/synthesis/SKILL.md](../src/inspect_audit/report_skills/synthesis/SKILL.md) | Legacy report-agent synthesis instructions. |
| [src/inspect_audit/skills/LICENSE.meridian](../src/inspect_audit/skills/LICENSE.meridian) | Upstream skill licence. |
| [src/inspect_audit/skills/VENDORED.md](../src/inspect_audit/skills/VENDORED.md) | Skill provenance, adaptation and default-loadout notes. |
| [src/inspect_audit/skills/analyzing-logs/SKILL.md](../src/inspect_audit/skills/analyzing-logs/SKILL.md) | Population analysis and transcript-processing guidance. |
| [src/inspect_audit/skills/analyzing-logs/scripts/append_to_notebook.py](../src/inspect_audit/skills/analyzing-logs/scripts/append_to_notebook.py) | Append cells to a Jupyter notebook from outside Jupyter. |
| [src/inspect_audit/skills/answer-format/SKILL.md](../src/inspect_audit/skills/answer-format/SKILL.md) | Checks whether answer packaging distorts grades. |
| [src/inspect_audit/skills/approach-census/SKILL.md](../src/inspect_audit/skills/approach-census/SKILL.md) | Classifies routes taken across attempts. |
| [src/inspect_audit/skills/contamination/SKILL.md](../src/inspect_audit/skills/contamination/SKILL.md) | Investigates evidence of recall/memorisation. |
| [src/inspect_audit/skills/environment-integrity/SKILL.md](../src/inspect_audit/skills/environment-integrity/SKILL.md) | Checks tools, services and environmental failures. |
| [src/inspect_audit/skills/failure-attribution/SKILL.md](../src/inspect_audit/skills/failure-attribution/SKILL.md) | Explains why recorded attempts failed. |
| [src/inspect_audit/skills/gold-answer/SKILL.md](../src/inspect_audit/skills/gold-answer/SKILL.md) | Checks the reference answer. |
| [src/inspect_audit/skills/ground-truth-access/SKILL.md](../src/inspect_audit/skills/ground-truth-access/SKILL.md) | Checks access to unintended answer-bearing information. |
| [src/inspect_audit/skills/insufficiently-specified/SKILL.md](../src/inspect_audit/skills/insufficiently-specified/SKILL.md) | Checks ambiguity and missing task constraints. |
| [src/inspect_audit/skills/map-inspect-packages/SKILL.md](../src/inspect_audit/skills/map-inspect-packages/SKILL.md) | Maps problems to Inspect ecosystem packages. |
| [src/inspect_audit/skills/other-findings/SKILL.md](../src/inspect_audit/skills/other-findings/SKILL.md) | Records evidence outside the named checks. |
| [src/inspect_audit/skills/reading-logs/SKILL.md](../src/inspect_audit/skills/reading-logs/SKILL.md) | Inspect log API guidance. |
| [src/inspect_audit/skills/red-teaming/SKILL.md](../src/inspect_audit/skills/red-teaming/SKILL.md) | Tests whether incorrect solutions can receive credit. |
| [src/inspect_audit/templates/grading.md](../src/inspect_audit/templates/grading.md) | Staged explanation of the benchmark grading interface. |

## Tests

| File | Purpose |
| --- | --- |
| [tests/conftest.py](../tests/conftest.py) | Shared fixtures. |
| [tests/test_audit.py](../tests/test_audit.py) | Assembling the audit task, and resolving what is being audited. |
| [tests/test_concordance.py](../tests/test_concordance.py) | Proving the audit's own grade channel before it accuses the benchmark. |
| [tests/test_contract.py](../tests/test_contract.py) | Recovering the declared tool surface from the registry. |
| [tests/test_fetch.py](../tests/test_fetch.py) | Log sources an audit can be pointed at, resolved before anything reads them. |
| [tests/test_grade.py](../tests/test_grade.py) | The grader judges the benchmark's world, never the audit's. |
| [tests/test_helpers/__init__.py](../tests/test_helpers/__init__.py) | Test helper package. |
| [tests/test_helpers/logs.py](../tests/test_helpers/logs.py) | Real tasks and real logs for tests. |
| [tests/test_investigate.py](../tests/test_investigate.py) | Exercise input isolation, publication and the real Docker/Quarto path. |
| [tests/test_item.py](../tests/test_item.py) | The filesystem an auditor gets for one item. |
| [tests/test_jobs.py](../tests/test_jobs.py) | Remote jobs: the policy over agent-written configs, staging, submission, ledger, collection. |
| [tests/test_provider_smoke.py](../tests/test_provider_smoke.py) | Opt-in paid provider check: INSPECT_AUDIT_LIVE_TESTS=1 pytest tests/test_provider_smoke.py. |
| [tests/test_registry.py](../tests/test_registry.py) | Replaying recorded exploits against a benchmark's own task. |
| [tests/test_report_components.py](../tests/test_report_components.py) | Report plots preserve source data; IRT rejects unusable populations. |
| [tests/test_run.py](../tests/test_run.py) | One end-to-end run under `mockllm`. |
| [tests/test_sandbox.py](../tests/test_sandbox.py) | The benchmark environment: reproduction, restoration, and unit conversion. |
| [tests/test_skills.py](../tests/test_skills.py) | The skills an auditor is given. |
| [tests/test_slice.py](../tests/test_slice.py) | Slicing real logs down to one item. |
| [tests/test_state.py](../tests/test_state.py) | The reconstructed benchmark session and its provenance discipline. |
| [tests/test_tools.py](../tests/test_tools.py) | Mirroring the evaluated agent's tools for the auditor to enact. |
| [tests/test_values.py](../tests/test_values.py) | The k8s emission of the audit sandbox: Helm values instead of a compose file. |

