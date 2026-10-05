---
name: view-results
description: "How to read the results of a Hawk job you submitted: its evals, its samples and their scores, and their transcripts. Read it after a job finishes."
---

# View Hawk Eval Results

When the user wants to analyze evaluation results, use these hawk CLI commands:


## In this container
This is the shape of reading a Hawk run's results, and it applies to the jobs you
submit. Every `hawk <verb> <eval-set-id>` in this file is `jobs(action="<verb>", label="<your job
label>")` for you: evals, watch, logs, trace, stacktrace, status, samples, transcript
(with `sample=<uuid>`), transcripts, wait, collect, stop. There is no `hawk` binary and
no login in this container; the tool runs the same commands on the operator's account,
restricted to the jobs you submitted. Anything the file describes that is not in that
list, you do not have.

Two differences that matter. You cannot list or read other people's eval sets, only your
own jobs, so ignore `hawk list eval-sets` and the search flags; `jobs(action="list")`
shows yours. And transcripts are written to files under
`/inputs/jobs/<label>/transcripts/` rather than printed, because a set of transcripts is
larger than anything you want in your context; read them with your own tools, and read
the `.eval` logs themselves after `jobs(action="collect")` when you want to count
anything.

The API environment table is not yours to change; the operator picked the deployment.

## 1. List Eval Sets

You can list all eval sets if the user do not know the eval set ID:

```bash
hawk list eval-sets
```

Shows: eval set ID, creation date, creator.

You can increase the limit of results returned by `--limit N`.

```bash
hawk list eval-sets --limit 50
```

Or you can search for a specific eval set by using `--search QUERY`.

```bash
hawk list eval-sets --search pico
```

## 2. List Evaluations

With an eval set ID, you can list all evaluations in the eval-set:

```bash
hawk list evals [EVAL_SET_ID]
```

Shows: task name, model, status (success/error/cancelled), and sample counts.

## 3. List Samples

Or you can list individual samples and their scores:

```bash
hawk list samples [EVAL_SET_ID] [--eval FILE] [--limit N]
```

## 4. Download Transcript

To get the full conversation for a specific sample:

```bash
hawk transcript <UUID>
```

The transcript includes full conversation with tool calls, scores, and metadata.

To get even more details, you can get the raw data by using `--raw`:

```bash
hawk transcript <UUID> --raw
```

### Batch Transcript Download

You can also download all transcripts for an entire eval set:

```bash
# Fetch all samples in an eval set
hawk transcripts <EVAL_SET_ID>

# Write to individual files in a directory
hawk transcripts <EVAL_SET_ID> --output-dir ./transcripts

# Limit number of samples
hawk transcripts <EVAL_SET_ID> --limit 10

# Raw JSON output (one JSON per line to stdout, or .json files with --output-dir)
hawk transcripts <EVAL_SET_ID> --raw
```

## Workflow

1. Run `hawk list eval-sets` to see available eval sets
2a. Run `hawk list evals <EVAL_SET_ID>` to see available evaluations
2b. or run `hawk list samples <EVAL_SET_ID>` to find samples of interest
3a. Run `hawk transcript <uuid>` to get full details on a single sample
3b. or run `hawk transcripts <eval_set_id> --output-dir ./transcripts` to download all
4. Read and analyze the transcript(s) to understand the agent's behavior

## API Environments

Production (`https://api.inspect-ai.internal.metr.org`) is used by default. Set `HAWK_API_URL` only when targeting non-production environments:

| Environment | URL |
|-------------|-----|
| Staging | `https://api.inspect-ai.staging.metr-dev.org` |
| Dev1 | `https://api.inspect-ai.dev1.staging.metr-dev.org` |
| Dev2 | `https://api.inspect-ai.dev2.staging.metr-dev.org` |
| Dev3 | `https://api.inspect-ai.dev3.staging.metr-dev.org` |
| Dev4 | `https://api.inspect-ai.dev4.staging.metr-dev.org` |

Example:
```bash
HAWK_API_URL=https://api.inspect-ai.staging.metr-dev.org hawk list eval_sets
```
