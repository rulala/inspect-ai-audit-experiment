---
name: answer-format
description: Establish whether the packaging of answers corrupted the measurement --
  right content graded wrong for its shape, or a format specification too ambiguous
  to follow. Investigate the scorer's parsing before judging any attempt.
metadata:
  grades: [PENALISED, AMBIGUOUS, SOUND, INCONCLUSIVE]
  unevidenced: [INCONCLUSIVE]
  tools: [attempt]
  details:
    instances: every formatting casualty, as {address, submitted, expected,
      content_right}
    examined: the transcript addresses you examined, one per attempt
---

# Did the shape of answers, not their content, decide grades?

A benchmark measures capability only if the scorer accepts every reasonable
packaging of a correct answer. This item owns the gap between what agents
submitted and what the parser accepts.

## First: what does the scorer accept?

Read the scorer's parsing and comparison code before reading any transcript
(`gold/grading.md` names the modules). Establish exactly what it extracts --
key names, types, tolerances, normalisation -- and what the task prompt told
agents about the required format. The gap between those two texts is where
this defect lives.

## Second: were correct answers failed for their shape?

Write and run a script enumerating all attempts and epochs, including missing
scores and errors. Review every actual submitted answer, whether credited or
rejected. Interpret its meaning independently of the recorded extraction; then
compare that interpretation, substantive correctness, extracted answer and score.
For non-text tasks, inspect the artifact or state that the scorer actually used.

Save an attempt-level CSV or JSON table and print it in bounded chunks so the
audit log retains it after sandbox cleanup. Include exact log/sample/epoch references,
submission evidence, recorded extraction/score, your interpretation and review
status. Classify justified credit/rejection, false credit/rejection, abstention,
scoring failure, and unresolved cases. Compute counts from the saved table;
report denominators and any unreviewed cases. Do not infer coverage from the
number of files or count the grader's own answers as independent verification.

For suspected formatting casualties, preserve the answer's content while changing
only its packaging, then `grade` it. Record both results and exact submissions.
Separate historical instances from authored probes and name the grader used.
Record correct-content rejections even when an explicit format requirement makes
them defensible: preserve the observation in `instances`, and explain the policy
judgment in `remarks`. A probe may establish behaviour without establishing any
historical score impact. Preserve unresolved cases rather than silently dropping them.

## Grade

- PENALISED -- at least one attempt's correct content was failed for its shape
- AMBIGUOUS -- the prompt's format specification underdetermines what the scorer
  accepts (cite both texts), but no graded casualty was demonstrated
- SOUND     -- complete review found no unjustified formatting rejection; retain
  defensible format-dependent rejections as observations
- INCONCLUSIVE -- incomplete coverage or failed checks prevent a conclusion
