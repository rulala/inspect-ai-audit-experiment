---
name: approach-census
description: Map how the field approached the item -- the routes attempts took,
  whether they converge on the path the task intends or scatter across others, and
  where the work actually happened. The census the other items read to know where
  to look; it judges behaviour, not success.
metadata:
  grades: [CONVERGENT, DIVERGENT, STALLED]
  details:
    routes: the distinct routes the field took, each as {route, share, scored} --
      route is what the attempt spent its moves on, share is roughly how much of the
      field took it, scored is how those attempts were graded
    intended: the route the task means the agent to take, in one line
---

# How did the field approach this item?

Every question has a route the task means the agent to take -- the work it tests.
This item does not judge whether that work was done; it maps what the field actually
did, so the other items know where to look. A route is what an attempt spent its
moves on, not what it answered: two attempts that both scored incorrect may have done
completely different things, and that split is the finding.

## Read the field, route by route

`samples_df("/audit/logs")` enumerates the attempts; the transcripts show the moves.
Group by route, not by score. Name each route in the field's own terms -- what it
ran, what it read, where it stopped -- and record roughly what share took it and how
that share scored. Common shapes: the intended path; a path around the work; the
environment failed and the attempt mined artefacts or thinned to a guess; never
executed at all. Say which one the task intended.

## Grade

- CONVERGENT -- the field concentrates on one route; name it and its share
- DIVERGENT  -- the field splits across distinct routes; name them and their shares
- STALLED    -- most attempts never get traction: never executed, or gave up early

There is no grade for "did well". This maps behaviour, not success -- a field that
converges and all fails and one that converges and all passes are the same grade
here. The score belongs to the routes; the why belongs to `failure-attribution`.
