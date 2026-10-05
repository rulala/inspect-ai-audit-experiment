---
name: investigating
description: Establish a benchmark's measurement claim, prioritise hypotheses and test them against code and recorded behaviour. Read at the start of an investigation.
---

## Orient

Establish what the benchmark claims to measure from the paper and README. Trace the
implementation: dataset, model-visible information, tools/environment, submission,
scorer and aggregation. Keep a short source-linked brief. Compare the pinned version
with the authoritative release; a historical defect may already be fixed.

Check that source, paper and logs describe comparable conditions. Read actual model,
judge, prompt, budget and metric configurations from log headers. Reproduce reported
numbers where the necessary evidence exists. An unexplained discrepancy remains a
limitation until you can establish its cause or bound its effect.

Inventory the population with sample summaries before loading whole transcripts.
Separate items, epochs, models and selected subsets. Count attempted/completed cases,
errors, unscored records, empty outputs and limit hits with explicit denominators.
Use reading-logs and analyzing-logs for the APIs; never unzip .eval files. A zero from
an extractor is not evidence of absence until it detects a known positive.

Then read selected transcripts across outcomes and configurations. Record observations
per attempt. Use them to generate hypotheses as well as checking hypotheses from code.
Inaccessible reasoning and inaccessible log sources are missing coverage.

## Prioritise and check

For each lead, state the suspected mechanism, what it would change about interpreting
the score, the strongest alternative explanation and the cheapest check that could
refute it. Prefer existing evidence and deterministic calculations before new runs.
Retire weak leads. A correct implementation is a useful conclusion when established.

Distinguish invalid successes from invalid failures. A pass may use an unintended
route; a failure may reflect scoring, environment, format or elicitation problems.
An auditor's inability to solve an item does not prove it unachievable. Check our own
parser, reconstructed tools, sandbox and configuration before blaming the benchmark.

Delegate item-level checks through inspect_audit/audit when useful. Select its items
(checks) and sample IDs for the question at hand; each auditor can inspect recorded
attempts and probe the benchmark scorer. Treat verdicts as leads: verify the evidence
behind consequential claims and examine some unflagged cases before estimating
coverage. Retain your own benchmark-wide analysis; item checks cannot establish
population representativeness or cross-model comparability by themselves.

For an experiment, record the hypothesis, changed variable, fixed baseline and outcomes
that would change your conclusion. Reuse fixed candidate answers when comparing
scorers. Changing candidates, prompts and budgets together cannot isolate a cause.
Read running-jobs for submission, smoke testing, collection and spending controls.
Without remote capability, record the proposed experiment rather than claiming it ran.

## Evidence

Use source for code claims, transcripts for behaviour and actual scorer outputs for
scoring claims. Keep locators, relevant quotations and rerunnable calculations.
Summaries and worker verdicts do not substitute for primary evidence. After compaction
or a challenge, re-read the supporting record rather than trusting your journal.

State numerator, denominator and selection method. Purposive examples do not estimate
prevalence, regardless of sample count. Distinguish measured score effects from
hypothesised consequences. Do not subtract headline scores from incomparable runs.
Check dataset identity before joining by sample ID, especially across revisions.

The findings register follows report/findings.schema.json. Supported/qualified claims
need evidence; source inspection, historical observations, experiments and audit
limitations have separate origins. Use the journal for actions and corrections, and
the register for what is currently supported. Save scripts and tables under
report/evidence. Read writing before drafting; presentation rules live there.

## Focused references

Load eval-validity-review for a measurement checklist, investigate-dataset for dataset
inspection, and security-audit-eval when the benchmark executes untrusted code. Use
them for relevant questions; do not adopt their alternative report formats. The
writing skill and supplied report schema define publication.
