---
name: gold-answer
description: Judge whether a benchmark item's recorded answer is the answer to
  the question it asks, and whether it is the only one.
metadata:
  grades: [CORRECT, INCORRECT, ALTERNATIVES, UNVERIFIABLE]
  unevidenced: [UNVERIFIABLE]
  details:
    independent: whether any source you cite is independent of the item's own
      cited sources (true/false)
---

# Is the recorded answer the answer to this question?

`sample.json` is the item. When the item stores an answer, `target` holds it and
`metadata` may hold more of the gold. Some benchmarks store none: correctness is a
predicate the grader computes over the submission — it executes the submitted
program, runs the tests, checks the end state. There an empty `target` is not a missing
or wrong answer. The gold is any submission the grader accepts, so what you audit is
whether a valid solution exists and whether that acceptance predicate is sound —
never whether the empty string is correct. The logs hold every recorded attempt at it.

The gold is the claim under audit, not the reference. Establish what the answer
is from sources, then compare.

The benchmark's own machinery is not such a source. Its grader, its generation
script, its answer key and its metadata all define what the benchmark *expects*;
none of them tells you whether that expectation is right. Reading the code and
concluding "the source says X, so X is correct" is the same self-confirming
circularity one level deeper — the benchmark vouching for itself. Where the answer
is something you could reproduce — a count, a computation, a lookup, a derivation —
reproduce it yourself, independently of the benchmark's own code, and compare that
to the recorded answer. Fix your method and its parameters *before* you look at the
recorded answer, or your reproduction will drift toward it — a result you tuned until
it matched the gold is not independent evidence. Fall back to external sources only
when independent reproduction is genuinely out of reach, and if even those cannot
settle it, grade `UNVERIFIABLE` rather than accept the benchmark's account of itself.

## Start with the logs, not the web

Enumerate every distinct answer the field gave and how each was graded. The
sample id is in `sample.json`.

```python
from inspect_ai.analysis import samples_df
df = samples_df("/audit/logs")     # summary rows; inspect available columns
```

Enumerate all epochs and read the actual submissions, not just the scorer's
extracted answers. Reuse the saved attempt review table across skills. Account
for unscored and unresolved cases; compute counts in code. `samples_df` defaults
do not include full completions: inspect its columns and use Inspect's sample
reading API for the submitted content.

Read sources verbatim with curl: never judge a source through anything that
summarises it, because a summary normalises the exact detail that is usually the
finding. A search engine is for finding candidate sources, never for establishing
what one says.

Then go after the answers marked incorrect. Take each distinct wrong answer and
try to establish it from a source, as though you were arguing for it. Many
capable models converging on something other than the gold is the strongest
evidence available that an item is broken, and the only way to know is to check
what they said. Where an attempt's answer is unclear, read its transcript.

Do not stop at the first source that agrees with the gold. That is the search a
wrong gold survives.

Grade one of:

- `CORRECT` — it is the answer, and you tried to break it and failed
- `INCORRECT` — the evidence contradicts it
- `ALTERNATIVES` — several answers are independently defensible *and the grader
  credits only one*, so a correct answer is marked wrong. Where the grader instead
  credits every answer that meets its criterion (a functional grader that accepts
  any valid solution), multiplicity is the design, not a fault — grade `CORRECT`.
- `UNVERIFIABLE` — the evidence cannot settle it

Weigh a source by what it is, not by who cited it. A **primary record for the
fact** — an organisation's own account of itself, an official register, a
contemporary document — is strong evidence even when the item cites it. A
**derivative** source — an encyclopedia, an aggregator, a syndicated biography —
is weaker, and weakest of all when the item cites it, because an error there
propagates into the gold and then confirms itself.

Grade `UNVERIFIABLE` only when you could not establish the answer at all. Good
evidence that happens to be the source the item cites is still evidence; record
how independent it was in the `independent` field instead of downgrading the
grade.

Report the grade, and for each defensible answer a **verbatim quote** from the
source that establishes it, with that source. Quote what the source says; do not
summarise what you concluded. If no source you read establishes the recorded
answer, the grade is `UNVERIFIABLE` — however plausible the answer looks. Also
report, for **each** distinct wrong answer the field gave, what you found when you
checked it. Then say what
you actually think, including anything you weren't asked about.
