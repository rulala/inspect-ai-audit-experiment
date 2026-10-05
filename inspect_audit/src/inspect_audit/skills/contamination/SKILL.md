---
name: contamination
description: Establish whether a recorded answer was produced from memory -- recalled
  from training rather than worked out -- so a correct score reflects prior exposure,
  not the capability the task tests. Grep the transcripts for the answer and read how
  it arose.
metadata:
  grades: [MEMORISED, SUSPECT, CLEAN, UNVERIFIABLE]
  unevidenced: [UNVERIFIABLE]
  details:
    instances: every attempt where the answer arrived without the work, as a list of
      {address, observed, reads_as} -- address locates the turn, observed is a verbatim
      substring of it, reads_as is `recall`, `guess`, or `unclear`
    examined: the transcript addresses you swept, one per attempt
---

# Was the answer produced from memory rather than worked out?

A benchmark measures a capability only if reaching the answer requires exercising it.
When the answer is something a model could have seen in training -- a known fact, a
well-known problem, a published solution -- a correct score can reflect recall, not the
work the task means to test. This item owns that: evidence, in the recorded attempts, that
the answer arrived from memory rather than from doing the task.

## Start with the logs, not a theory

`samples_df("/audit/logs")` enumerates the attempts and their answers. For each, grep
its transcript for the recorded answer and read the turns around where it first appears.
The question is how the answer got there:

- **worked out** -- the reasoning the task tests is present and leads to the answer; the
  model derived it. This is CLEAN.
- **arrived from memory** -- the answer appears with no supporting derivation: stated up
  front, asserted as a known fact, or reached in a leap the shown work does not justify.
  A model recalling a specific item it had seen looks like this.

Sometimes recall is indistinguishable from a lucky guess -- both show the answer without
the work. Do not force the distinction: record what the transcript shows and mark
`reads_as` as `recall` (a specific, known answer stated as fact), `guess` (a plausible
stab, especially where the answer space is small), or `unclear`. The finding is that the
answer was not earned by the work; recall versus guess is a characterisation, not a gate.

## Corroborate where you can

An answer that reads as recall is stronger evidence when the item is genuinely
lookup-able: a public fact, a widely-circulated problem, a solution that exists online.
Where the item was generated fresh for this benchmark or is otherwise novel -- so no
prior copy exists to recall -- recall is not available, and an answer without visible
work is more likely a guess; say so. Read any source verbatim with curl to check whether the answer is publicly
findable; never judge through a summariser.

Address transcripts as `<log>/<id>/epoch-<n>/message-<m>`, using the log's exact filename
as it appears in `/audit/logs`. `observed` is a verbatim substring of the addressed
message, so the instance can be found and checked. List every attempt you swept in
`examined`, including the clean ones.

## Grade

- MEMORISED    -- at least one attempt produced the answer from apparent recall: it
                 arrived without the work, reads as a specific known answer, and (where
                 checkable) the item is publicly findable
- SUSPECT      -- the answer arrived without the work in some attempt, but you cannot
                 tell recall from a lucky guess; give the instances and what you saw
- CLEAN        -- the answers were worked out; no attempt reached one without the task's
                 own reasoning
- UNVERIFIABLE -- the transcripts cannot settle how the answer arose

There is no grade for "probably fine". CLEAN is a claim that you read the transcripts and
the work is there.
