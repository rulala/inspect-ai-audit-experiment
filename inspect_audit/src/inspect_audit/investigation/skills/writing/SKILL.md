---
name: writing
description: How to write the audit report so a reader who knows the domain but did not watch you work gets the findings, their size and their evidence with no wasted words. Read before drafting report.qmd.
---

## Who reads it and why

The reader is deciding whether to trust a number. They know what a benchmark is. They
did not watch you work and do not care how you worked, except in the short method note.
They want to know what you found, how big it is, how you know, and what would change
your mind. Give them that, in that order, in every section.

## Sentences

Lead with the finding and its size. The first sentence of a section or paragraph is
the conclusion; support follows. One idea per sentence, plain words, an active verb.
Around twenty words; split anything that needs a semicolon. No em dashes; use a
comma or a full stop. British English.

Numbers carry their denominators and their scope in the same sentence or the next one:
"812 of 1,000 attempts" not "81%" alone; "among scored attempts" when that is the
population. Three or more numbers go in a table, not a sentence.

Do not narrate your process in the body. "I reviewed", "I inspected", "the audit
examined" belong in the method note or nowhere. State what is the case and cite where
it is shown. Do not pad claims with qualifications that name no alternative: a
qualification is worth writing only when it says what would change the conclusion.
Say "not assessed" and why, in one sentence, when you did not check something.

## Structure and figures

The report is one document: Summary, Benchmark architecture, a divider, then Full
audit with the supplied detailed sections. The summary introduces the benchmark in
2–3 sentences, then gives one linked bullet per substantive finding, positive or
negative. Each bullet leads with the conclusion and evidence or magnitude, with its
scope. Finish with a short coverage statement. Put execution failures and detailed
methods in the full audit; mention them briefly above only when they limit findings.

Every detailed finding explains the mechanism, evidence locator, affected population,
score consequence (or unmeasured consequence), and limitations. Add explicit anchors
so the brief links directly to the relevant finding. Group repeated instances.

Use components.py and styles.css for consistent graphics. Read the component APIs
before plotting. Architecture uses Graphviz automatic layout: visible agent context
in a shaded boundary, hidden scoring inputs outside, labelled information flows.
Keep evidence/architecture.json with source locators and mark unverified edges.

Use outcome_bars for outcome populations, paired_plot for controlled comparisons,
response_matrix for model-by-item responses, and the existing chart/table/transcript
helpers where appropriate. Components retain data/settings beside graphics. Captions
state denominators and uncertainty methods; narrative interpretation belongs in prose.
Use labels as well as colour. Never invent error bars or add a graphic to fill space.

irt_analysis.py provides optional GIRTH Rasch/2PL fitting from a binary response CSV.
Read its API and diagnostics. Only use it with diverse, comparable model configurations
and enough coverage. Repeated epochs are not independent models. Error/missing records
must not become incorrect responses. Document the score encoding, model families,
settings and assumptions; a fit does not establish unidimensionality or independence.
Report unstable or unsuitable fits as such. No IRT analysis is required.

## What every report has to state

Somewhere the reader can find it, and once only: what you ran and what you read. How
many samples out of how many, on what dates, with what model versions and package
revisions, the total tokens and the cost. If you ran jobs yourself, they are in the
ledger and the collected logs; take the numbers from there, not from memory.

Compare scores numerically only when the conditions support an interpretable
comparison. Otherwise state which candidate, judge, dataset or budget differs without
subtracting unrelated headline scores.

Say the size of what you tested. A hypothesis checked on eight samples is reported as
eight samples. Anything under roughly twenty cases, or fewer than three cases per
subgroup you are comparing, is examples rather than a rate, and the sentence says so.

## Before publishing

Read the rendered report with render_report. Remove every drafting comment. Check each
number against the table it came from. Look at each figure with view_image. Then
publish.
