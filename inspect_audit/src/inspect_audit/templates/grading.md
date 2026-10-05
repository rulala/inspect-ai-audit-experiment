# How this item is graded

Scorers on the live task:

{scorers}

Do not take this file's word for anything. The logs in `logs/` are real Inspect `.eval`
files, headers untouched, sliced to this item only — every claim about how these
attempts were graded is checkable there.

```python
from inspect_ai.log import read_eval_log, read_eval_log_sample

log = read_eval_log("logs/<name>.eval", header_only=True)
log.eval.scorers        # what graded these attempts: grader model, grading template
log.plan.steps          # how the model was elicited: the solver chain
log.plan.config         # and with what config (reasoning effort, token budget)
log.eval.packages       # what was installed when it ran

s = read_eval_log_sample("logs/<name>.eval", id=..., epoch=1, resolve_attachments=True)
s.messages, s.events    # the full trajectory, not just the answer
s.scores                # the score, its answer, and the judge's explanation
```

Across every attempt at once:

```python
from inspect_ai.analysis import samples_df
samples_df("logs")
```

Grading is recorded **per log**, so read it per log rather than assuming the field was
graded uniformly: different runs of the same benchmark are routinely graded by
different judge models or judge templates.

The benchmark's own code is staged in this sandbox, so read the real grader:

```bash
ls benchmark/          # the scorers' modules and the task's own directory
```

Where the benchmark is an installed package, its modules resolve too:

```bash
python -c "import importlib, inspect; [print(inspect.getsourcefile(importlib.import_module(m))) for m in '{modules}'.split()]"
```

Prefer `benchmark/`: a benchmark that is a loose repository rather than a published
package will not import here, and the staged copy is the same source either way.

Read the grading template in full before deciding whether an answer was fairly marked.
A template that pins an exact string, omits a valid alternative, or collapses "refused"
into "incorrect" fails answers that were right.

## What the target is, and is not

`sample.json` is this item as the benchmark defines it, in Inspect's own shape — load it
with `json_dataset()` to get a `Sample`. Its `target` is not necessarily the whole gold:
benchmarks keep reference material in whatever shape suits them, such as a patch, a test
file, an acceptable numeric range, or a list of accepted alternatives. This item's
metadata keys are: {metadata_keys}. If one of those looks like reference material, treat
it as part of the gold.

If the metadata cites URLs, those are the benchmark's own sources, so they are the
*weakest* confirmation available: the question was probably written from them, which
makes agreement circular. An independent source is worth more.
