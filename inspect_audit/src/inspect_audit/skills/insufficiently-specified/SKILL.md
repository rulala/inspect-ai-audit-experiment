---
name: insufficiently-specified
description: Establish whether the question pins down a unique answer. When a question
  leaves choices open that change the result -- method, parameters, data, tie-breaks --
  a correct analysis can land on a value the key does not accept, and the benchmark
  grades the guess of a convention, not the work.
metadata:
  grades: [UNDERSPECIFIED, BORDERLINE, SPECIFIED]
  details:
    freedoms: the choices the question leaves open that change the answer, each as
      {choice, answer_it_yields} -- a decision and the value a defensible analysis
      reaches by making it
    settles_to_gold: what the question would have to fix for the gold answer to be
      the determined one
---

# Does the question determine a single answer?

A benchmark measures capability only if each question has one answer a competent
worker must reach. This item owns the opposite: a question that admits several
defensible answers because it leaves the analysis underdetermined, so matching the
gold depends on guessing the author's unstated choices.

## First: what is actually being asked?

Read the question and the recorded gold (`sample.json`). State precisely what it asks
for and what answer it treats as correct. Read `gold/grading.md` to see how narrowly
the answer is compared -- a tight scorer over a loose question is where this defect
bites hardest.

## Second: find the open choices

Establish the work the question requires, then find every decision left to the worker
that changes the result: the method or tool, a parameter or threshold, which data or
reference set, a tie-break or rounding convention, an ambiguous referent. For each,
ask what a defensible analyst could choose and what answer that choice yields. A
choice that does not move the answer does not count -- only degrees of freedom that
reach a different value.

## Third: does a correct analysis miss the key?

If a competent worker following the question as written could reach a value the
scorer rejects, the question is underspecified: it is testing whether the agent
guessed the author's convention, not whether it did the work. Say what the question
would have had to fix -- which choice pinned down -- for the gold to be the only
answer.

## Grade

- UNDERSPECIFIED -- a defensible analysis of the question as written reaches an answer
                   the scorer rejects; give the open choices and the answers they yield
- BORDERLINE     -- the question leaves a choice open, but one reading clearly
                   dominates and the others are strained; name the choice and why
- SPECIFIED      -- the question determines a single answer; competent work converges

There is no grade for "hard question". Difficulty is not underspecification -- a
question can be very hard and still have one determined answer. Grade the
determinacy, not the difficulty.
