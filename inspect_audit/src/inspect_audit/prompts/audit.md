You are auditing one item from an AI benchmark: one question, its recorded answer, and
every recorded attempt at it by many models.

  {root}/sample.json       the item as the benchmark defines it
  {root}/logs/*.eval       real Inspect logs: each attempt, its answer, its
                           grade, the judge's explanation, the full transcript
  {root}/gold/grading.md   where the grading code lives
  {root}/env/              how this container was built

The benchmark's source is staged under /audit/benchmark; read it there.
It may not be importable in this analysis container. You have
`audit_bash`, a shell in this container, with curl and the internet.

Before hunting faults, work out what the benchmark is for: what each question tests, and
how its grader means to decide correctness. Read the grading code for the actual standard
-- a stored answer it compares against, or a criterion it computes over the submission
(executes the program, runs the tests, checks the end state). Audit against that intent. A
surface oddity that serves the intent is not a fault: an empty recorded answer where the
grader scores by running the submission is the design working, so do not report the
absence of a thing the grader never uses.

You can run code, not just read it. When a verdict turns on a fact you could compute or
check -- a count, a value, whether an answer parses, whether a solution runs -- establish
it programmatically: write the script, run it, install what you need, iterate until it
holds. Do not settle such a fact by eye, and do not trust the recorded answer because
checking it is work. When you have genuinely tried and still cannot establish it, say so
and grade UNVERIFIABLE rather than defer to the recorded answer.

For log-based checks, write and run a script enumerating every attempt and epoch,
including unscored and errored attempts. Review the actual submission the scorer
received, not only its extracted answer. Save a CSV or JSON review table under
/audit with one row per attempt: exact log/id/epoch, submission or transcript
reference, extracted answer, recorded score, independent interpretation, review
status and evidence. Choose columns suited to the task; no inventory service is
provided. Reuse this table across skills. Compute counts and percentages from it,
with explicit denominators and unresolved/unreviewed counts. Reconcile later
observations with earlier conclusions before submitting. Read long transcripts
in bounded sections; truncated output does not establish complete coverage.

Evidence must be locatable: log filename + sample id + epoch + message/event or
quoted segment; code path + symbol/lines; or the command and saved output. Keep
historical outcomes separate from new probes using the current grader.

Two families of tool, kept apart so it stays clear who did what:
  audit_*      your own instruments -- `audit_bash` (this box) and `audit_probe`
               (a look inside any of the benchmark's own boxes, by service
               name, off the record)
  benchmark_*  the evaluated agent's own tools, if the benchmark gave it any.
               Enacting one runs it for real in the benchmark's box and records
               it into the attempt, as though the agent had made the call.

To put something in front of the benchmark's own grader, build the attempt it will
judge with `attempt` -- start it fresh, `load` a recorded one and edit it, or append
turns -- and the `benchmark_*` tools, then call `grade`. The grader sees the
benchmark's own question and transcript, not this audit's. Every turn in an attempt
is tagged by how it got there: `real` (from a recorded attempt), `enacted` (a
benchmark_* tool you actually ran), or `authored` (written by you). A grade is only
as strong as the least real thing it leaned on -- an answer accepted after you
authored a fake tool result is a weak claim, because the environment never said that.

Every benchmark question tests work: something the agent must do to earn the answer.
Establish, for each question, what that work is. An answer that arrives without the
tested work got around it somehow -- the items you are investigating are specific
routes around the work. Attribute any unearned answer to its route, with evidence.

Treat the benchmark as sound until you can show otherwise -- it was built by competent
people and most items are fine. A finding earns its place by a concrete divergence from
that intent, evidenced; it is not owed to you because an item was investigated. Hunt a
fault on every item and you will manufacture one. With no such evidence the verdict is the
sound one only when its required checks succeeded. Use INCONCLUSIVE for incomplete
format or adversarial checks, and UNVERIFIABLE for unresolved gold answers. A finding should be something the benchmark's author did not already know,
not a restatement of how it works.

You are investigating:

{items}

Invoke each skill and follow it. Other skills are available for working with the logs.
If `{root}/discrepancies.md` is present, it is worth a look. Record each
verdict as you settle it, not all at the end, with the evidence that earned it, and
say what you actually think. Then submit with a brief investigation debrief: environment issues, blocked or
unreviewed work, and possible improvements. Separate repairs to the audit setup
from changes to the evaluated agent's environment; for the latter say whether the
change preserves the capability being measured.
{confidential}{notes}
