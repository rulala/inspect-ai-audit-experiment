---
name: synthesis
description: Turn a directory of completed audit logs into a run-level report --
  grade matrix first, then corroborated findings with cited evidence. Compute
  every number from the full matrix; never extrapolate from transcripts you
  happened to read.
---

# Synthesizing an audit run

You are reading the logs of `inspect_audit/audit` runs. In each `.eval`, one
sample is one audited benchmark item, and the audit items investigated (gold-answer,
red-teaming, answer-format, ...) appear as that sample's **scores**: one score
column per audit item, whose value is the auditor's grade and whose metadata
carries `evidence` (verbatim observations, each with its source address),
`approaches`, `tried`, and `remarks`. `NO_VERDICT` means the auditor never
recorded one -- treat it as missing coverage, not as a clean bill.

## Order of operations

1. **Inventory** -- headers only (`read_eval_log(f, header_only=True)`): which
   task was audited, which model audited it, status, sample counts. Say up
   front what the run covers and what it doesn't.
2. **Grade matrix** -- one frame, samples x audit items, from
   `read_eval_log_sample_summaries()` (grades and evidence live in the score
   summaries; full samples are not needed for this). Materialize it to
   `/report/data/matrix.csv` before interpreting anything.
3. **Tallies and anomalies** -- grade distributions per item, items where
   grades disagree across samples, samples flagged by several items at once.
   Grade vocabularies differ per item: enumerate the values you actually see
   before interpreting them.
4. **Drill down** -- only now open transcripts, and only for flagged samples
   (`read_eval_log_sample(f, id=...)`). The auditor's evidence quotes its
   source address; verify the quote against the transcript before repeating it.
5. **Findings** -- write as you go, not at the end.

## Rules of evidence

- A run-level finding needs **two independent samples** showing it, or your own
  re-verification in the primary source. Anything single-source goes in a
  separate "uncorroborated" list, labelled as such.
- Every claim cites log file + sample id (+ the evidence's source address).
  Never cite a transcript you have not opened.
- Every number comes from the full matrix, computed, with its denominator. A
  rate over fewer than 15 observations is an anecdote and is labelled one.
- Say what you did not check. Missing coverage (NO_VERDICT, errored samples,
  items never run) is itself a finding.

## Deliverable

Maintain `/report/report.md` incrementally: findings ranked by severity, each
with grade counts, confidence, and its citations. Persist every computed table
as CSV under `/report/data/` so the analysis is re-runnable. The report should
survive the session dying at any moment.

## Working with the operator

The operator directs the session over chat. Follow their direction, but
volunteer surprises, propose the next analysis when one is obvious, and answer
with what the matrix says rather than what would be plausible. Honest
uncertainty beats coverage theatre.
