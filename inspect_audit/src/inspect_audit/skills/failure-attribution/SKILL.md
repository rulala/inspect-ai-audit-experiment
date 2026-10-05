---
name: failure-attribution
description: For the attempts that failed, establish why -- a genuine capability limit
  (the field did sound work and the answer did not come) or a benchmark artefact
  (something another item owns broke the attempt). The honest counterweight to the
  defect items, since one honest answer is that the benchmark is fine and the models
  simply cannot do this.
metadata:
  grades: [CAPABILITY, ARTEFACT, MIXED, UNCLEAR]
  unevidenced: [UNCLEAR]
  details:
    attributions: each distinct cause of failure, as {route, why, owner} -- why the
      attempts on that route failed, and owner is the audit item that owns it if an
      artefact (environment-integrity, gold-answer, answer-format,
      insufficiently-specified), or `capability` when the work was simply not done
---

# Why did the failing attempts fail?

A bank of items that each hunt a defect is biased toward finding one. This item is
the counterweight: its job is to attribute failures honestly, and one honest answer
is that the failures are real -- the field took a sound path and could not complete
the work, and the benchmark is measuring what it claims. Do not manufacture a defect
to explain a hard question.

## Attribute, do not re-grade

Take the attempts graded incorrect and, route by route (see `approach-census`),
establish why each failed. Separate two causes. A **benchmark artefact** is a failure
the attempt could not have avoided: the environment broke, the question was ambiguous,
the gold was wrong, the format was penalised. A **capability limit** is an attempt
that did reasonable work on the intended path and still did not reach the answer.

You do not settle an artefact here -- that belongs to the item that owns it. Point at
it with enough evidence to be worth its attention, and record the owner. The
transcripts decide: a failure that reproduces the same missing dependency across the
field is an artefact; a field that each reaches a different wrong answer by sound
reasoning is a capability limit.

## Grade

- CAPABILITY -- the failures are genuine; name what the work required that was not met
- ARTEFACT   -- the failures trace to a defect; give the route and the owning item
- MIXED      -- both are present; attribute each share
- UNCLEAR    -- the transcripts cannot settle why

There is no grade that blames the benchmark by default. CAPABILITY is a real verdict,
and often the right one -- a question can be hard because it is hard.
