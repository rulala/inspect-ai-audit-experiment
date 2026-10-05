# The investigator

The registered `inspect_audit/investigate` task reads a benchmark's source and
supplied Inspect logs, analyses them in a Docker workspace, and, when `hawk_api_url`
is set, runs things on Hawk: the agent writes an ordinary Hawk eval-set config (the
benchmark itself, or inspect_audit's sample auditors over recorded attempts) and
`hawk_submit` checks it against a policy and submits it; `jobs` waits, collects and accounts.
It writes a Quarto HTML report with a short finding brief, a benchmark architecture
diagram, and the full six-section audit, then exits by default;
explicit interactive mode waits through ACP.

## Remote work

The agent's shell runs in a container with no credentials. The dispatch tools run in
the Inspect process on this machine and use the `hawk` CLI (your login, in its keyring)
without exposing that login to the container. Local input logs remain local. For remote
auditors, import the corpus once with `hawk import /path/to/logs --name my-corpus`,
then pass the resulting `hawk:imported-...` address in `logs`. Reuse that address
across investigations. Hawk establishes the access metadata and warehouse records;
a raw upload to an arbitrary S3 prefix does neither reliably. Existing imported logs
should be referenced, not reimported. Historical model names must be registered in
Middleman for their log access permissions to resolve.

A submitted config is treated as hostile input. It is parsed with Hawk's own schema and
then checked as the thing that will actually run: allowlisted packages (the task package
and inspect_audit only), task registry names, models, images, secrets
(`OPENROUTER_API_KEY` only) and runner environment keys, with the same rules applied to
task arguments, where a `model`, an `image`, a size or a log source can otherwise be
overridden past the outer fields. Task-level secrets, isolation, runner image, cpu,
agents, solvers and unknown keys are refused. Runner memory and log_model_api are allowed. `epochs`, `token_limit` and
`time_limit` are required and capped, and a null `limit` is not a size.

Remote spend uses reservations, not a guaranteed dollar ceiling. Inspect enforces
`cost_limit` during solving; scoring occurs outside that scope, and in-flight calls
can overshoot. We reserve `cost_limit x samples x models x epochs x retry attempts`,
multiplied by `1 + number of model_roles` as a scoring buffer. That buffer is a
planning assumption, not an enforced cap on arbitrary scorer code. Set conservative
grader generation limits and keep experiments small. The reservation stays held
until `jobs(action="collect")` downloads the logs (to `/inputs/jobs/<label>/`, read-only
in the box) and records what it really cost. If a model in a collected job has no
registered price the cost stays unknown and the reservation stands, rather than an
estimate being written down as a measurement. The check and the record happen in one
locked ledger transaction, so two submissions in flight cannot both take the last of the
allowance.

The eval set id is assigned at submission, one per job: a reused id makes Hawk resume
that set. Imported logs keep their existing Hawk source. Storage, warehouse indexing
and read permission are separate: a staged prefix is not necessarily a readable
Hawk eval set. Remote inputs are checked at setup by listing a sample and retrieving
its transcript; `seed.evidence_access` records unavailable sources before the lead
model starts. This probe does not establish complete corpus coverage. Every accepted submission is
saved under `<investigation>/jobs/` and recorded in `jobs.json`, written as `pending`
before the CLI call, so a submission whose response is lost is reconciled against Hawk
rather than lost or sent twice. `resume=<investigation dir>` carries on in an existing
directory: same inputs, workspace, journal and ledger, no restaging, pending jobs
reconciled first. Worker models are restricted to `worker_models`.
Models are routed straight to OpenRouter with `OPENROUTER_API_KEY` from `secrets_file`;
the middleman is bypassed with `HAWK_RUNNER_REFRESH_URL: ""`. Required with
`hawk_api_url`: `task_package`, the git spec Hawk runners install to run the audited task.

## Run

Install the package in your Inspect environment and start Docker. Model credentials
belong to the local Inspect runner, not the research container. Choose any model
supported by that environment (including an already configured Middleman route).

```bash
inspect eval inspect_audit/investigate \
  -T repo=/absolute/path/to/benchmark-repo \
  -T logs='["/absolute/path/to/benchmark-logs", "/absolute/path/to/audit-logs"]' \
  -T overview="Investigate scoring reliability and failed runs" \
  -T output_dir=/absolute/path/to/investigations \
  -T budget_usd=10 \
  --model YOUR_MODEL --display plain

# For an interactive run add: -T interactive=true --acp-server
# Then attach from another terminal with: inspect acp
```

`repo` also accepts an HTTPS Git URL. Optional arguments: `revision`; `paths` (repository
paths to include, so the snapshot holds the task under audit rather than every eval in a
collection); `target_task`; `paper` (a local file, or a URL downloaded at setup, arXiv
abstract pages resolving to their PDF); `docs` (documentation directories mounted read-only
at `/inputs/docs/<name>`, e.g. the Inspect docs and Hawk's, which the agent is told to read
before using the APIs); `extra_skills` (skill directories); `token_limit` (none by default).
Local repositories are snapshotted with `git archive` at HEAD or `revision`: dirty
files, untracked files and submodule contents are excluded. Remote cloning is done
by the investigator in its sandbox; it records the resolved commit. Read access to
private HTTPS repos is not configured automatically.

Batch mode is the default and exits after publication. Use `-T interactive=true`
with `--acp-server` to opt into discussion. In interactive
mode publication is followed by an ACP input wait; attach with `inspect acp` and
decline/cancel the input request to end the discussion. The process must remain
alive to retain the same live session. Automatic crash recovery is not yet wired.

## Artifacts and limits

Each invocation creates a unique directory under `output_dir`, recorded in the
Inspect task metadata as `investigation_dir`. `inputs/` contains selected inputs
and the seed manifest; the container mounts it read-only. `work/` is a persistent
writable workspace. `published/<version>/` contains the Quarto source, rendered
HTML, a snapshot of the findings register, figures and any evidence the investigator included. Published
versions are outside the container's mounts. The agent cannot overwrite them.

The image installs Inspect, pandas, pyarrow, matplotlib, Jupyter, PDF text extraction
and Quarto 1.9.38. The first build downloads dependencies. No host Docker socket,
home directory or provider credentials are mounted into the research container.

`budget_usd` is enforced by default through Inspect's cost limit, covering this
investigator's own model calls (child jobs, when they exist, will be budgeted separately).
OpenRouter's current prices are registered at setup so any `openrouter/...` model has a
price; for other providers supply `--model-cost-config`. Set `-T enforce_cost_limit=false`
to make it a planning number only. The `budget` tool shows spend by model and says
"unknown" rather than zero when a model has no price. There is no token cap unless
`token_limit` is set.
A four-hour working-time limit applies independently, including when dollar enforcement
is disabled. Override it through Inspect when deliberately running longer. Limits include follow-up conversation; reaching one
can end the session before publication, but the draft workspace remains on disk.
Docker/storage costs are not included. This is not a global or provider-enforced cap.

The report starts with 2–3 sentences introducing the benchmark and linked finding
bullets, including positive findings. A standard architecture diagram shows agent-visible
inputs, hidden scorer inputs and feedback. A divider separates the brief from the full
audit: The task; The grader; The harness and environment; Aggregation and limits;
How agents approach it; How it is built. Shared components cover outcome bars, paired
comparisons, response matrices and transcript excerpts, retaining the plotted data.
Optional IRT screens for adequate comparable configurations before fitting; it is not
a required report section. Graphviz and the optional reporting dependencies are installed
in the investigator image. Editorial judgement remains in the writing skill.

There are two working memory artifacts: `work/journal.md` and
`work/report/findings.json`. Publication validates the register against the bundled
schema and checks evidence file paths. Supported/qualified findings require evidence;
ids must be unique. Evidence must resolve inside the report or `/inputs`; referenced
inputs are copied into the published bundle and that snapshot's paths are rewritten.
Location strings and quotes are not automatically verified against .eval events yet.

Rendering checks that HTML can
be built; it does not verify findings. Independent verification/rubric grading and
Hawk orchestration remain separate future changes. Extra skills cannot enable tools
that this task does not provide.

## Validate without model spend

```bash
uv run pytest tests/test_investigate.py -m 'not docker'
uv run pytest tests/test_investigate.py -m docker
```

The Docker tests read a staged Git snapshot, create a chart, render Quarto, and
verify the exported HTML after sandbox cleanup. A second test drives the actual
agent loop with scripted model responses to load a skill, read real Inspect logs,
check the budget and publish. They do not measure the investigator model's quality
or exercise a live ACP client; the discussion transition is tested separately.

## Remaining boundaries

The outer investigator is local-only. Task construction still creates the workspace
before evaluation starts, and reconstructing the task creates a new directory.
Log files are hardlinked into read-only inputs on the same filesystem, with copying
only across filesystems; use completed immutable logs, since hardlinks share changes
made by another host process. Resuming is explicit: pass `resume=<investigation dir>`;
there is no automatic retry.
The research container uses a per-run Compose network with internet egress.
Its Inspect image install still uses the host version; a development-only host build
needs an explicit image strategy before this outer task is moved to Hawk.

The older `inspect_audit/report` interactive log session remains compatible. Shared
publication, findings validation and ACP input handling now live in `_report.py`;
`investigate` adds source orientation and autonomous work. This is not yet a unified
replacement for every report entry point. Rediscovery input/web exposure policy must
be selected before a graded run; no graded run is launched by this command.

## Experiment configuration and checks

When the target task is known, `work/jobs/templates/benchmark.yaml` and `audit.yaml`
are generated from the active worker menu and policy and validated with Hawk's
installed schema. Copy one, choose the judge deliberately, then set task arguments
and sample selection. The audit starter does not attach logs automatically.

`working_limit` measures active work, excluding semaphore/rate-limit waits;
`time_limit` includes those waits. Use a generous wall-clock allowance and expand in
bounded batches. The installed Hawk runner controls `max_samples` in infrastructure
configuration, so it cannot be overridden at job top level.

The verdict tool transports its extensible `details` object as a JSON string for
strict-provider compatibility. The tool decodes and validates the object before
recording it; stored verdicts still contain structured dictionaries.

Run the ordinary checks with `make check`, `make test`, and `make test-docker`.
The opt-in provider check sends synthetic inputs, incurs a small model charge, and
requires `OPENROUTER_API_KEY` in the environment:

```bash
INSPECT_AUDIT_LIVE_TESTS=1 .venv/bin/pytest tests/test_provider_smoke.py
```

Remote worker fixes must be committed/published and `audit_package` pinned to that
revision before a Hawk run can use them. A local editable install alone does not
update a runner's package.

## Instruction ownership

The system prompt contains the remit, stages and workspace/trust boundaries. The
`investigating` skill owns scientific method and evidence; `running-jobs` owns remote
operations; `writing` owns presentation. Focused reference skills load on demand.
Four older overlapping workflow copies remain in the repository for reference but
are not mounted by default. Optional skills can be supplied through `skills`.

`inspect_audit/report` is an older, separate conversational task for ACP clients.
It is not a stage called by `investigate`; new autonomous work uses `investigate`.
