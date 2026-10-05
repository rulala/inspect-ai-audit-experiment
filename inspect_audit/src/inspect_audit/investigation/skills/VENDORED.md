# Vendored skills

The investigator mounts eleven skills. `investigating` and `writing` are ours. The other
nine are copied from other people's repositories and adapted: each keeps its original
text and gains an `## In this container` section saying what applies here, what does
not, and which of its commands exist as tools.

| Skill | From | Commit |
| --- | --- | --- |
| eval-validity-review | UKGovernmentBEIS/inspect_evals `.claude/skills` | 67a9ea5 (2026-08-26) |
| investigate-dataset | same | 67a9ea5 |
| security-audit-eval | same | 67a9ea5 |
| check-trajectories-workflow | same | 67a9ea5 |
| eval-report-workflow | same | 67a9ea5 |
| read-eval-logs | same | 67a9ea5 |
| view-results | METR hawk `.claude/skills` | 45f629c (2026-08-25) |
| debug-stuck-eval | same | 45f629c |
| babysit-eval | same | 45f629c |

Both source repositories are MIT licensed (Copyright (c) 2024 UK AI Security Institute;
Copyright (c) 2026 METR). `reading-logs`, `analyzing-logs` and `map-inspect-packages`
are mounted too and live in `../../skills` with their own note.

## What was changed

Nothing substantive: the phases, checklists and vocabulary are the authors' own. The
edits are the ones a reader in this container would otherwise have to make in their
head.

- **Paths.** `src/inspect_evals/<eval_name>/` is the task's directory under
  `/inputs/source`; `NOTES.md` is `/workspace/journal.md`; `agent_artefacts/…` output
  directories are gone. `uv run python` is `python`.
- **The user.** These skills stop to ask a user which eval to look at, whether to
  overwrite a file, how many samples to run. There is nobody to ask, so the preamble
  says decide and record it.
- **Report formats.** Four of them end by writing their own report with their own rating
  scale. The report structure is fixed by the `writing` skill, so each preamble maps the
  skill's output onto the six sections and the findings register instead.
- **Commands.** The Hawk skills name `hawk` CLI verbs. There is no `hawk` binary in the
  box; the preamble maps each verb onto `jobs(action=…)`, which runs the same command on
  the operator's login restricted to this investigation's own jobs. Where a capability
  is genuinely absent (interactive ACP babysitting, Inspect Scout, `inspect trace
  anomalies`, `hawk list eval-sets` across the deployment), the preamble says so
  plainly rather than leaving the agent to discover it.

Two skills are read for their thinking rather than run: `check-trajectories-workflow`
(the invalidates-a-success versus invalidates-a-failure distinction, and its five
per-transcript categories) and `eval-report-workflow` (the reproducibility block, the
delta against the published number, and its minimum-size rule).

Vendored 2026-09-09.

## Default loadout after the September 10 cleanup

The source copies above are retained for provenance and optional use. Four overlapping
workflows are no longer mounted by default: check-trajectories-workflow,
eval-report-workflow, read-eval-logs and babysit-eval. The maintained investigating,
writing and running-jobs skills cover the workflow; reading-logs/analyzing-logs provide
the Inspect APIs. Focused validity, dataset, security and Hawk diagnostic references
remain available. INVESTIGATION_SKILLS in _investigate.py is the authoritative loadout.
