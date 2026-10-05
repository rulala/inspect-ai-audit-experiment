---
name: environment-integrity
description: Establish whether the environment failed the agents -- tools,
  dependencies, or infrastructure erroring for reasons that are the benchmark's
  fault rather than the agent's. Reproduce failures in the benchmark container
  where possible.
metadata:
  grades: [BROKEN, FLAKY, SOUND]
  details:
    instances: every environment-caused failure, as {address, tool, error, cause}
    examined: the transcript addresses you examined, one per attempt
    repro: for each distinct failure, the command you ran in the benchmark
      container and whether the failure reproduced
---

# Did the environment fail the agents?

An attempt can only demonstrate capability if the environment holds up its end.
This item owns faults that belong to the benchmark: dependencies its setup
promised but did not deliver, tools that error for infrastructure reasons,
permissions and timeouts the agent could not have avoided.

## First: what does the environment promise?

Read the environment definition (`/audit/env/`) and the task's instructions.
Establish what an agent was entitled to assume works: the declared image, the
documented commands, the intended execution path.

## Second: what failed, and whose fault was it?

Sweep every attempt for tool and command failures. Separate environment faults
(promised dependency missing, image defect, permission error, infrastructure
timeout) from agent faults (wrong flags, invented paths, misread instructions)
-- only the former are instances. Record the error text verbatim.

Then reproduce: run the same failing command in the benchmark container and
record whether it fails there too. A failure that reproduces from a fresh
container is the benchmark's; one that does not needs an explanation before it
counts.

Address transcripts using the log's exact filename as it appears in
`/audit/logs`. When in doubt, record the instance -- flagged instances get
validated downstream; silent misses do not.

## Grade

- BROKEN -- an environment fault blocked the intended path in at least one attempt
- FLAKY  -- environment faults occurred but a reasonable agent could route around them
- SOUND  -- observed failures were agent-caused, or none occurred
