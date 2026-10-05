---
name: eval-report-workflow
description: "What a results report has to state to be reproducible: samples over total, dates, versions, tokens and cost, and the measured number next to the published one. Read it when writing up anything you ran yourself."
---

# Make an Evaluation Report

This workflow drives [`tools/evaluation_report.py`](../../../tools/evaluation_report.py), which reads a per-eval `report_config.yaml` and produces a full reproducible `report.md` (results table, reference comparison, per-category breakdowns, token totals, approximate cost) plus header-only JSON copies of the input logs under `results/`. The `report_config.yaml`, regenerated `report.md`, and `results/` folder are committed alongside the eval's `eval.yaml`.


## In this container
This is a maintainer's workflow for putting a results table in an `inspect_evals`
README, driven by `tools/evaluation_report.py`, which is not in this container. You are
not writing that report and your structure is fixed elsewhere. Three things in it are
worth taking.

Reproducibility block. Whatever you report, say how many samples out of how many, on
what dates, with what package versions and models, at what total token count and cost.
If you ran jobs on Hawk, those numbers are in the logs you collected.

Comparison against the source. Put the paper's or leaderboard's number next to the one
you measured, with the delta and where the reference came from. An unexplained delta is
a lead, and often the most valuable single row in an audit.

Size honesty. It calls anything under twenty samples, or under three times the number of
meaningful subgroups, too small to conclude from. Apply that to your own runs: if you
tested a hypothesis on eight samples, the report says eight samples and says what that
does and does not support.

If the eval is LLM-judged and you can construct or find an oracle-labelled set, judge
calibration against it is the strongest grader evidence available to you.

## Report Formatting

The evaluation report included in the README.md is the rendered `report.md` produced by [`tools/evaluation_report.py`](../../../tools/evaluation_report.py). It should run on the entire dataset or $5 of compute per model, whichever is cheaper. Use the token count from smaller runs to make this prediction.

A typical rendered report looks like this:

```markdown
# Evaluation Report

## Implementation Details

Brief description of any deviations from the paper, known limitations, etc.

## Results

| Model         | Inspect (accuracy) | Reference | Δ      | Samples | Stderr | Time |
| ------------- | ------------------ | --------- | ------ | ------- | ------ | ---- |
| openai/...    | 0.600              | 0.580     | +0.020 | 100/100 | 0.049  | 18s  |
| anthropic/... | 0.400              | 0.420     | -0.020 | 100/100 | 0.049  | 6s   |

_Reference: Paper, Table 3_

## Reproducibility Information

- Samples: 100 / 100 per model
- Run dates: 2026-04-29
- Versions: inspect_ai=0.3.x, inspect_evals=0.x
- Models: ...
- Total tokens: 1,234,567
- Approximate cost: $0.42 USD (prices as of 2026-04)

Reproduction commands: ...
```

**Register entries:** for register entries (`register/<name>/eval.yaml`), populate the optional `evaluation_report` block in `eval.yaml` instead of editing `README.md` directly — the README is regenerated from the YAML by `make check`. The block accepts `timestamp`, a `results` list (with `model`, `accuracy`, and optionally `provider`, `stderr`, `time`, `date`), and `notes`. Extra fields at either level are allowed for eval-specific metric columns. See `register/README.md` for the schema.

If the eval.yaml file includes an arXiv paper, check that paper for the models used and human baselines. Include the human baseline in the notes section if it is present. If you can, select three models that would be suitable to check if this evaluation successfully replicates the original paper, including at least two different model providers.

If you cannot do this, or if there aren't three suitable candidates, use frontier models to fill out the remainder of the list. Currently, these are as follows:

### Frontier Models

See `references/frontier-models.md` for the current list of frontier models and their costs. This file should be updated when models or prices change.

## Workflow Steps

01. Set up the working directory:

    1. If the user provides specific instructions about any step, assume the user's instructions override these instructions.
    2. If there is no evaluation name, ask the user for one.
    3. The evaluation name should be the eval folder name plus its version (from the @task function's version argument). For instance, GPQA version 1.1.2 becomes "gpqa_1_1_2". If this exact folder name already exists, add a number to it via "gpqa_1_1_2_analysis2". This name will be referred to as `<eval_name>`.
    4. Create a folder called `agent_artefacts/<eval_name>/evalreport` if it isn't present.
    5. Whenever you create a .md file as part of this workflow, assume it is made in `agent_artefacts/<eval_name>/evalreport`.
    6. Copy EVALUATION_CHECKLIST.md to the folder.
    7. Create a NOTES.md file for miscellaneous helpful notes. Err on the side of taking lots of notes. Create an UNCERTAINTIES.md file to note any uncertainties.

02. Read the [Evaluation Report Guidelines](../../../EVALUATION_CHECKLIST.md#evaluation-report-guidelines).

03. Check to see if the README for the evaluation already has an evaluation report. If so, double-check with the user that they want it overwritten.

04. Read the main file in EVAL_NAME, which should be src/inspect_evals/EVAL_NAME/EVAL_NAME.py in order to see how many tasks there are.

05. Perform an initial test with 'inspect eval inspect_evals/EVAL_NAME --model gpt-5.1-2025-11-13 --limit 5' to get estimated token counts. Use -T shuffle=True if possible to produce random samples - to see if it's possible, you'll need to check the evaluation itself.

06. Perform model selection as above to decide which models to run.

07. Select a method of randomisation of the samples that ensures all meaningfully different subsets of the data are checked. This is as simple as ensuring the dataset is shuffled in most cases. Explicitly track how many meaningfully different subsets exist.

08. Tell the user what commands to run and how much compute it is expected to cost.

    Base your compute calculation on the most expensive model in your list. Each model should be run on the same dataset size regardless of cost, hence we limit it via the most expensive one. If the task will be more expensive than $5 per model to run the full dataset, give the user your estimate for the cost of the full dataset. This means if there are multiple tasks to run, you'll need to split the cost among them according to token usage in the initial estimate. You should assume Gemini reasoning models take roughly 10x the tokens of the other models when performing these calculations.

    As a rough example, tell the user something like this:

    > I recommend running N samples of this dataset, for an estimated compute cost of $X. I recommend the following models: `<list of models>`. I recommend them because `<reasoning here>`. The full dataset is expected to cost roughly `<full_cost>` for all three models. This is based on running `<model>` for 5 samples and using Y input tokens and Z output tokens. Please note that this is a rough estimate, especially if samples vary significantly in difficulty.
    >
    > The command to perform this run is as follows:
    >
    > `inspect eval inspect_evals/<eval_name> --model model1,model2,model3 --limit <limit> --max-tasks <num_tasks> --epochs <epochs>`
    >
    > After you have run the command, let me know and I'll fill out the evaluation report from there.

    Num tasks should be number of models run * the number of @task functions being tested. Epochs should be 1 if the limit is below the size of the full dataset, or as many epochs as can be fit into the cost requirements otherwise.

    Make sure to give them the command in one line or with multiline escapes to ensure it can be run after copy-pasted.

    If the number of samples recommended by this process is less than 20, or less than (3 * meaningful subcategories), you should also inform the user that the number of samples achievable on the $5/model budget is too small for a meaningful evaluation, and that more resources are needed to test properly. A minimum of 20 samples are required for error testing. If they need help, they can ask the repository's maintainers for testing resources.

    If the user asks you to run this for them, remind them that they won't be able to see the progress of the evaluation due to the way Inspect works, and asking if they're sure. Do not proactively offer to run the command for them.

09. Once the eval has been run, create `src/inspect_evals/<eval_name>/report_config.yaml` with the headline metric, any `reference_results` from the original paper or leaderboard, a `reference_source` citation, and `notes` describing implementation details and any deviations. The schema is defined by `tools.report_utils.ReportConfig`; see [tools/README.md](../../../tools/README.md#evaluation_reportpy) for the full set of fields.

10. Run the report script, passing in the `.eval` files produced by the run:

    ```bash
    python tools/evaluation_report.py src/inspect_evals/<eval_name>/report_config.yaml \
      --logs logs/file1.eval logs/file2.eval logs/file3.eval
    ```

    The script writes `src/inspect_evals/<eval_name>/report.md` and a header-only JSON copy of each input log under `src/inspect_evals/<eval_name>/results/<task>/<YYYY-MM-DD>_<model>.json` (the machine-readable companion). If any problems arise, ask the user to give you the relevant information manually.

11. Splice the contents of `src/inspect_evals/<eval_name>/report.md` into the README.md in the appropriate section, and commit `report_config.yaml`, `report.md`, and the `results/` folder alongside `eval.yaml`. Then tell the user the task is done. Do not add human baseline or random baseline data unless they already appear in the README.

12. (Optional) If the evaluation uses an LLM judge and an oracle log exists or can be created (e.g., by running the eval with a stronger reference judge, or by collecting human labels via `--oracle-labels`), run `python tools/judge_calibration_diagnostics.py <eval_logs> --oracle-log <reference_log>` to produce calibrated estimates with confidence intervals. Include findings in the evaluation report notes if relevant. See [tools/README.md](../../../tools/README.md) for details.
