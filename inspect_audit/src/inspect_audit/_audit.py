import atexit
import os
import shutil
import tempfile
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from inspect_ai import Task
from inspect_ai.agent import as_solver
from inspect_ai.analysis import EvalModel, EvalTask, SampleSummary, samples_df
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import EvalLog
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import SandboxEnvironmentType

from ._agent import audit_agent, audit_items, item_scorer
from ._contract import task_contract
from ._item import ANSWER_METADATA, AUDIT_ROOT, AttemptRef, AuditItem, item_sample
from ._resolve import resolve_task
from ._sandbox import (
    audit_compose,
    audit_values,
    has_benchmark,
    run_benchmark_setup,
    sample_sandbox,
)

LogSource = str | list[str] | EvalLog | list[EvalLog]


@solver
def benchmark_setup() -> Solver:
    """Run the audited sample's own setup script in the benchmark service."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        await run_benchmark_setup((state.metadata or {}).get("benchmark_setup"))
        return state

    return solve

ITEM_PROMPT = (
    f"You are auditing one benchmark sample. Its audit filesystem is at {AUDIT_ROOT}."
)


# logs read per samples_df call: the read fans out one open file descriptor per
# log with no cap, so an unchunked directory of N logs needs N+7 fds -- at ~1200
# logs that exhausts the default limit and killed a box with `Errno 24`. Derived
# from the process's own soft fd limit (leaving headroom for pandas/pyarrow and
# the eval's own writers), overridable for a tight or generous environment.
_FD_RESERVE = 64


def _log_chunk_size() -> int:
    override = os.environ.get("INSPECT_AUDIT_LOG_CHUNK")
    if override and override.isdigit() and int(override) > 0:
        return int(override)
    import resource

    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    return max(32, soft - _FD_RESERVE)


def _log_files(logs: LogSource) -> list[Any] | None:
    """Resolve a directory or file-list to concrete log files, else `None`.

    `None` means "hand it to samples_df whole" -- an already-read `EvalLog`, or
    a resolution that failed (never swallow the failure into a single chunk that
    re-arms the fd exhaustion this exists to prevent).
    """
    from inspect_ai.analysis._dataframe.util import resolve_logs
    from inspect_ai.log import EvalLog, list_eval_logs

    if isinstance(logs, EvalLog) or (
        isinstance(logs, list) and any(isinstance(x, EvalLog) for x in logs)
    ):
        return None
    # a directory is listed with list_eval_logs: inspect's own resolve_logs
    # stat()s the path first, and an S3 prefix has no object to stat (HeadObject
    # 404), so a log directory in a bucket fails there while listing it works.
    # explicit files still go through resolve_logs, which normalises their order
    # the way samples_df does, so chunking cannot reorder attempts.
    if isinstance(logs, str) and not logs.endswith((".eval", ".json")):
        return sorted(info.name for info in list_eval_logs(logs, recursive=True))
    return list(resolve_logs(logs))


def attempts(
    logs: LogSource,
    *,
    sample_ids: Collection[str] | None = None,
    task: str | None = None,
) -> pd.DataFrame:
    """One row per recorded attempt, with a `score_*` column per scorer.

    Args:
        logs: Log directory, log files, or already-read `EvalLog`s.
        sample_ids: Restrict to these sample ids.
        task: Restrict to logs recording this task. Sample ids are only unique
            within a task, so a logs directory holding another task's logs would
            otherwise silently attach that task's attempts to these items.
            Matched on the unqualified name, so `pkg/name` in a log joins a task
            resolved as `name` and vice versa.
    """
    columns = SampleSummary + EvalModel + EvalTask
    files = _log_files(logs)
    if files is None:
        frame = samples_df(logs, columns=columns)
    else:
        size = _log_chunk_size()
        chunks = [files[i : i + size] for i in range(0, len(files), size)] or [[]]
        frames = [samples_df(chunk, columns=columns) for chunk in chunks]
        frame = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if task is not None and not frame.empty:
        tails = frame["task_name"].astype(str).str.split("/").str[-1]
        matched = frame[tails == task.split("/")[-1]]
        if matched.empty:
            found = ", ".join(sorted(frame["task_name"].astype(str).unique()))
            raise ValueError(
                f"None of these logs record task {task!r} (they record: {found})."
            )
        frame = matched
    if sample_ids is not None and not frame.empty:
        wanted = {str(sample) for sample in sample_ids}
        frame = frame[frame["id"].astype(str).isin(wanted)]
    return frame.reset_index(drop=True)


def audit_task(
    task: str | Task,
    logs: LogSource | None = None,
    *,
    samples: Sequence[str | int] | None = None,
    limit: int | None = None,
    items: list[str] | None = None,
    task_args: dict[str, Any] | None = None,
    sandbox: SandboxEnvironmentType | None = None,
    solver: Solver | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    notes: str | None = None,
    confidential: bool = False,
    redact: Sequence[str] | None = None,
    attempts_task: str | None = None,
    auditor_image: str | None = None,
    benchmark_image: str | None = None,
) -> Task:
    """Build the audit as an Inspect `Task`.

    Args:
        task: The task to audit -- registry name, `file.py@name`, or a `Task`.
        logs: Logs providing the recorded attempts at each sample.
        samples: Sample ids to audit (defaults to all, subject to `limit`).
        limit: Audit at most this many samples.
        items: Audit items to investigate (defaults to all of them).
        task_args: Task arguments used to resolve the audited task.
        sandbox: Override the sandbox (defaults to the audited task's own, else ours).
        solver: Override the auditor (defaults to `audit_agent()`).
        model: Model to audit with (defaults to the evaluated model).
        reasoning_effort: Reasoning effort for the auditor model, when it takes one.
        notes: A free-form operator steer inserted into the auditor's system prompt.
        confidential: The benchmark is unpublished -- instruct the auditor not to
            transmit item content off the box.
        attempts_task: Name of the task whose attempts to join, when the logs record
            a sibling variant of the audited task rather than the task itself. Many
            benchmarks ship the same items under several variants (tools vs no-tools,
            ablations); the field's answers to an item are evidence about that item
            whichever variant produced them, and each sliced log keeps its own header
            so the auditor can see which variant it is reading. Defaults to the
            audited task's own name, which is the safe choice.
        redact: Further metadata keys to strip from the item the auditor reads, on
            top of the answer-bearing keys always stripped. Use it for a key that
            would pre-empt the judgement under audit as well as for one that carries
            the answer.
        auditor_image: Emit the sandbox as Helm values for k8s providers, with this
            published image as the auditor (see `audit_values`).
        benchmark_image: Published image standing in for benchmark services that
            `build:` their own (k8s only).
    """
    target = resolve_task(task, task_args)
    staging = _staging()
    redacted = (*ANSWER_METADATA, *(redact or ()))
    contract = task_contract(target)

    # one merged compose per distinct environment: ctf-style benchmarks give every
    # sample its own compose file, most give them all one
    composed: dict[str, SandboxEnvironmentType] = {}

    def environment(sample: Sample) -> SandboxEnvironmentType:
        if sandbox is not None:
            return sandbox
        spec = sample_sandbox(target, sample)
        key = str(spec.config) if spec is not None and isinstance(spec.config, str) else ""
        if key not in composed:
            stage = staging / "sandbox" / str(len(composed))
            if auditor_image is not None:
                composed[key] = audit_values(
                    target,
                    spec,
                    stage=stage,
                    auditor_image=auditor_image,
                    benchmark_image=benchmark_image,
                )
            else:
                composed[key] = audit_compose(target, spec, stage=stage)
        return composed[key]

    # select samples before reading attempts: staging is pushed down to the
    # selection (the summary read over the logs is not -- every log's summaries
    # are still read once, chunked so file handles stay bounded)
    dataset = list(target.dataset)
    ids = [
        str(sample.id) if sample.id is not None else str(index)
        for index, sample in enumerate(dataset, start=1)
    ]
    chosen = {str(s) for s in samples} if samples is not None else None
    selected = [sid for sid in ids if chosen is None or sid in chosen]
    if limit is not None:
        selected = selected[:limit]
    in_scope = set(selected)

    # group the attempts by sample
    by_sample: dict[str, list[AttemptRef]] = {}
    if logs is not None:
        frame = attempts(logs, sample_ids=in_scope, task=attempts_task or target.name)
        scores = [str(c) for c in frame.columns if str(c).startswith("score_")]
        for row in frame.to_dict("records"):
            sample_id = str(row["id"])
            by_sample.setdefault(sample_id, []).append(
                AttemptRef(
                    model=str(row["model"]),
                    epoch=int(row["epoch"]),
                    scores={name.removeprefix("score_"): str(row[name]) for name in scores},
                    log_file=str(row["log"]),
                    sample_id=sample_id,
                )
            )
        if not by_sample and in_scope:
            raise ValueError(
                f"No attempts at any selected sample of '{target.name}' were found in "
                "these logs. Check the task and its arguments match the logs."
            )

    # stage one audit sample per item
    audit_samples: list[Sample] = []
    any_benchmark = False
    for sample_id, sample in zip(ids, dataset, strict=True):
        if sample_id not in in_scope:
            continue
        item = AuditItem(
            task=target.name,
            task_args=task_args or {},
            sample_id=sample_id,
            attempts=by_sample.get(str(sample_id), []),
        )
        original_env = sample_sandbox(target, sample)
        benchmark = sandbox is None and has_benchmark(original_env)
        any_benchmark = any_benchmark or benchmark
        audit_samples.append(
            item_sample(
                target,
                sample,
                item,
                prompt=ITEM_PROMPT,
                stage=staging / str(sample_id),
                sandbox=environment(sample),
                original_env=original_env,
                benchmark=benchmark,
                redact=redacted,
                contract=contract,
            )
        )

    any_media = any(
        any(f"{AUDIT_ROOT}/media/" in key for key in (s.files or {}))
        for s in audit_samples
    )

    return Task(
        name=f"audit/{target.name}",
        dataset=MemoryDataset(audit_samples),
        setup=benchmark_setup(),
        solver=solver
        or as_solver(
            audit_agent(
                items=items,
                model=model,
                reasoning_effort=reasoning_effort,
                notes=notes,
                confidential=confidential,
                media=any_media,
                benchmark_scorers=target.scorer,
                contract=contract,
                benchmark=any_benchmark,
            )
        ),
        scorer=[item_scorer(item) for item in audit_items(items)],
        metadata={"audited_task": target.name},
    )


def _staging() -> Path:
    # one directory per run, removed at exit; INSPECT_AUDIT_KEEP_STAGING=1 keeps it
    staging = Path(tempfile.mkdtemp(prefix="inspect_audit_"))
    if not os.environ.get("INSPECT_AUDIT_KEEP_STAGING"):
        atexit.register(shutil.rmtree, staging, ignore_errors=True)
    return staging
