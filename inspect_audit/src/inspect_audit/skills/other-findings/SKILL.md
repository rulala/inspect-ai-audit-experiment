---
name: other-findings
description: Record what you noticed that none of the other audit items would have
  caught. The item set is a list of defects someone already knew to look for; this
  one is for the defect nobody wrote an item for yet.
metadata:
  grades: [FOUND, NONE]
  unevidenced: [NONE]
  details:
    findings: every observation, as a list of {observed, source, why_it_matters,
      scope} -- scope is `item` when it is a fact about this item alone, or
      `benchmark` when the same thing would show up on other items too
    not_pursued: anything you noticed but could not establish, in one line each,
      so a later pass knows where to look
---

# What did you find that nobody asked you about?

The other items each name a defect someone already knew to look for. A benchmark's
worst problems are usually not on that list -- they are the thing that looked odd for
ten seconds while you were doing something else. This item is where that goes.

Review what you actually inspected: the item, grading code, and recorded attempts.
Do not assume another check established exhaustive coverage.
Report what struck you that the other items had no slot for.

## What counts

Anything about this benchmark that a careful reader of your report would want to know
and would not learn from the graded items. Some shapes worth naming, not a checklist:

- the **construction** shows through -- an artefact of how the item was generated,
  a template that leaks, a difficulty label that encodes something other than
  difficulty
- the **field behaves strangely** in a way that is not about right or wrong: everyone
  hedging identically, a model refusing, answers clustering on a value the question
  never mentions
- the **measurement** does not mean what the benchmark says it means, even though
  the item and the grader are both fine
- the **harness** did something the benchmark's own docs say it does not, or the
  code contradicts its own comments
- an item that is **too easy for its level**, or trivially solvable by a route the
  question did not intend

## What does not count

Do not restate a verdict you already recorded under another item. If it belongs to
`gold-answer` or `red-teaming` or any other item you were given, it goes there and
not here. This is the residue, not a summary.

Do not report an impression you could not check. An observation with no source is a
line in `not_pursued`, not a finding.

Read `/audit/discrepancies.md` if it is there. It is a mechanical diff of the tools
the benchmark's code declares against the tools the logs show reaching the model. A
mismatch you can confirm -- a tool the field used that the benchmark never declared,
or a declared one that never ran -- is a `benchmark`-scope finding.

## Scope, and why it matters

Mark each finding `item` or `benchmark`. A fact about this one question is worth
recording; a fact that would recur across the bank is worth far more, because
downstream it becomes one finding with many demonstrations rather than many findings.
You are auditing one item and cannot see the others, so say `benchmark` when the
mechanism you found is not specific to this question -- and say what you would expect
to see elsewhere if you are right.

## Grade

- FOUND -- you have at least one observation with a source
- NONE  -- you looked and there is nothing the other items did not already cover

There is no grade for "probably fine". NONE is a claim that you looked.

Keep audit-environment failures in the submission debrief. A missing analysis
package, unavailable judge, or misconfigured API route is not by itself a defect
of the benchmark. Historical judge differences are a lead: establish their effect
before asserting measurement corruption. Keep incomplete investigations in
`not_pursued`, with the next check that would resolve them.
