# Activity journal

Append actions and corrections with evidence references.

## 2026-10-04 orientation and initial checks

- Cloned source at `0dacad3c3bacfb50308de94eb8b8f9e368cfe30c` (seed specified HEAD). ARC Easy loads `allenai/ai2_arc`, config `ARC-Easy`, test split, pinned dataset revision `210d026...`; transforms source answer labels to the corresponding displayed A–D position; uses `multiple_choice(cot=False)` and `choice()` with no tools or sandbox.
- Supplied log is a successful `limit=5`, one-epoch run of `inspect_evals/arc_easy`, task comparability version 2, package `inspect_evals 0.23.0`, `inspect_ai 0.3.276`, model `openai/gpt-5.6`. It has 5/5 completed/scored, no errors, and accuracy 1.0. This is 5/2,376 test items (0.21%), selected by the limit rather than a declared random sample.
- Read all five recorded transcripts. Each visible prompt contains question, choices and format instruction but no target. Model emitted exactly `ANSWER: <target>` on all five; logged scores mark all correct. No observed leakage or target/scorer mismatch in this purposive five-item slice.
- Correction/limitation: direct pinned-dataset loading via `datasets.load_dataset` was unavailable because the optional `datasets` package is not installed. Testing direct download from the exact Hugging Face revision instead.

### Claims inventory (current)

| # | Claim | Source | Category | Verification result | Severity |
|---|---|---|---|---|
| 1 | ARC uses natural science questions to evaluate knowledge and reasoning | ARC README lines 1–3 | Capability | Code and five logged items are consistent; broader content coverage not yet checked | — |
| 2 | ARC dataset has 7,787 grade-school multiple-choice science questions | ARC README Dataset section | Provenance/size | Eval metadata gives Easy test=2,376 and Challenge test=1,172; 7,787 evidently refers to all splits/configs and is not the task population | — |
| 3 | Simple accuracy is calculated over datapoints | ARC README Scoring | Scoring | Confirmed in log: choice scorer accuracy over 5 scored samples | — |
| 4 | Supplied 1.0 result measures ARC Easy performance | implied interpretation to test | Aggregation | Not supported: log is limit=5 of 2,376, non-random first slice | Medium if generalized |
- Population result: 2,376/2,376 records transform with answer present; 97 numeric-label records and 11 non-four-choice records are handled. Five exact duplicate question pairs exist. All five logs exactly join source.
- Parser probe correction: scorer alignment is sound, but the upstream parser rejects bare/prose letters. Classified low severity because the prompt explicitly requires `ANSWER:` and observed effect is 0/5.
- No remote jobs commissioned; evidence was sufficient and budget pricing was unknown.
