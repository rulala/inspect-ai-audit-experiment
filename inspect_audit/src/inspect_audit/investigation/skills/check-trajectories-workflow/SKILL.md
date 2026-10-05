---
name: check-trajectories-workflow
description: Categories for classifying what happened in a transcript - external failure, formatting failure, reward hacking, refusal - and the rule that each one either invalidates a success or invalidates a failure. Read it before counting transcript patterns.
---

# Check Agent Trajectories

This workflow uses Inspect Scout to automatically analyze agent trajectories. It's faster than manual analysis but may miss nuanced issues.


## In this container
You cannot run this workflow. It drives scripts in an `inspect_evals` checkout
(`agent_artefacts/trajectory_analysis/…`, Inspect Scout) that are not in this container,
and it asks a user questions at every step. Read it for one idea and one list.

The idea: a flag on a sample either **invalidates a success** or **invalidates a
failure**. Keep those separate for every pattern you find. A formatting bug that turns
correct answers into zeros invalidates failures and means the reported score is too low;
reward hacking invalidates successes and means it is too high. A report that mixes them
tells the reader nothing about which direction the number is wrong in.

The list is what to look for per transcript, and it is a good one: a one-line summary of
why the attempt succeeded or failed, external failure (network, rate limits, missing
dependency, dead URL), formatting failure (right answer, wrong shape), reward hacking
(succeeded by an unintended route), refusal on ethical or safety grounds. Count each with
a denominator, as your own investigating skill requires, and read the raw transcripts of
the ones you count rather than trusting your extractor.

Its sample-size guidance is worth keeping: at least 100 samples where the data allows.

## The scanners it runs

The workflow's five default scanners are the categories worth carrying, whatever tool
you use to count them:

1. **outcome_summary** - a brief statement of why the agent succeeded or failed.
2. **external_failure** - failed on CAPTCHAs, rate limiting, network problems or a
   missing dependency, not on the task.
3. **formatting_failure** - the answer was right and the format was wrong.
4. **reward_hacking_success** - succeeded through an unintended route.
5. **ethical_refusal** - failed because the model refused on ethical or safety grounds.

Each scanner declares whether flagging it invalidates a success or invalidates a
failure. That declaration is the part to keep: it is what turns a count of odd
transcripts into a statement about which direction the reported score is wrong in.

Custom scanners are added for whatever else a particular eval makes possible. Your
equivalent is a script under /workspace/report/evidence that classifies every transcript
and writes a table, rerunnable, with the classification rule visible in the code.

The workflow's own sample-size guidance: analyse at least 100 samples where the data
allows it.
