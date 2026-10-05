# Vendored skills

`reading-logs`, `analyzing-logs` and `map-inspect-packages` are copied verbatim from
[meridianlabs-ai/inspect-skills](https://github.com/meridianlabs-ai/inspect-skills),
MIT licensed, Copyright (c) 2026 Meridian Labs. The licence is in `LICENSE.meridian`.

They are vendored rather than rewritten because they already carry the log-reading
API and its traps — never unzip an `.eval`, read `header_only` first, pass
`all_samples_required=False` for a non-success log, and pyarrow `<NA>` comparisons
raise rather than returning `False`. An auditor needs that and we would only be
paraphrasing it less accurately.

Vendored 2026-08-17.
