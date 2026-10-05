import importlib
import inspect as _inspect
import json
from collections import defaultdict
from collections.abc import Collection
from logging import getLogger
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai._eval.task.util import task_run_dir
from inspect_ai.dataset import Sample
from inspect_ai.event import (
    Event,
    EventTreeNode,
    EventTreeSpan,
    ModelEvent,
    event_tree,
)
from inspect_ai.log import (
    read_eval_log,
    read_eval_log_samples_by_id,
    write_eval_log,
)
from inspect_ai.util import SandboxEnvironmentType, resource
from inspect_ai.util._sandbox.environment import resolve_sandbox_environment
from pydantic import BaseModel, Field

from ._contract import SolverContract, discrepancies_doc
from ._sandbox import BENCHMARK_SERVICE, compose_renames

logger = getLogger(__name__)

AUDIT_ROOT = "/audit"

# metadata fields that carry a benchmark's own answer -- redacted from the item the
# auditor reads (SWE-bench stores the gold patch and hidden tests here). the grader
# still sees them via `benchmark_metadata`; this only blinds the auditor.
#
# these are the shapes we have met; a benchmark that keeps its answer, or the finding
# under audit, under some other key extends them with `audit_task(redact=...)`. an
# unredacted key that pre-empts the auditor's judgement does not produce a wrong
# verdict, it produces an unfalsifiable one.
ANSWER_METADATA = ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS")

GRADING_TEMPLATE = Path(__file__).parent / "templates" / "grading.md"


class AttemptRef(BaseModel):
    """One recorded attempt at an item: which log holds it, and how it scored."""

    model: str
    epoch: int
    scores: dict[str, str] = Field(default_factory=dict)
    log_file: str
    sample_id: str | int


class AuditItem(BaseModel):
    """The item an audit sample is auditing, carried in its `Sample.metadata`."""

    task: str
    task_args: dict[str, Any] = Field(default_factory=dict)
    sample_id: str | int
    attempts: list[AttemptRef] = Field(default_factory=list)


def item_sample(
    task: Task,
    sample: Sample,
    item: AuditItem,
    *,
    prompt: str,
    stage: Path,
    sandbox: SandboxEnvironmentType | None = None,
    original_env: SandboxEnvironmentType | None = None,
    benchmark: bool = False,
    redact: Collection[str] = ANSWER_METADATA,
    contract: SolverContract | None = None,
) -> Sample:
    """One audited item, as an Inspect `Sample`."""
    files = item_files(
        task,
        sample,
        item.attempts,
        stage=stage,
        original_env=original_env,
        redact=redact,
        contract=contract,
    )
    # the benchmark's own sample metadata, for its grader (base_commit, the recorded
    # answer, whatever the scorer reads). carried on every item, not just the ones with
    # a benchmark container: a task with no sandbox still has a grader, and `grade`
    # hands it this. never staged to the filesystem, so redaction does not apply.
    metadata: dict[str, Any] = {
        "audit_item": item.model_dump(),
        "benchmark_metadata": dict(sample.metadata or {}),
        # the original session's raw material, so the benchmark's own TaskState can
        # be rebuilt at grading time: a grader must never see the audit's input,
        # choices or messages in place of the benchmark's
        "benchmark_input": sample.input
        if isinstance(sample.input, str)
        else [message.model_dump(exclude_none=True) for message in sample.input],
        "benchmark_choices": list(sample.choices) if sample.choices else None,
    }

    # the benchmark's environment is image plus per-sample state: forward the
    # original sample's files into the benchmark's service(s), and carry its
    # setup script for `benchmark_setup` to run there
    if benchmark:
        run_dir = Path(task_run_dir(task))
        renames = compose_renames(original_env)
        # carry the file spec too, not just stage it: a hard reset rebuilds the box
        # from its image (empty), so `phoenix_benchmark` must re-lay these files the
        # way sample-init first did -- restoring only setup would drop file-delivered
        # state the evaluated agent started with
        benchmark_files: dict[str, str] = {}
        for name, value in (sample.files or {}).items():
            # a `service:` prefix names one of THEIR services: route it through
            # the same rename the compose merge applied (their default became
            # `benchmark`; a sibling keeps its name). blanket-prefixing here
            # once produced `benchmark:victim:/flag` -- a file literally named
            # `victim:/flag` written into the wrong box.
            service, sep, box_path = name.partition(":")
            if sep:
                key = f"{renames.get(service, service)}:{box_path}"
            else:
                key = f"{BENCHMARK_SERVICE}:{name}"
            anchored = _anchored(value, run_dir)
            files[key] = anchored
            benchmark_files[key] = anchored
        if benchmark_files:
            metadata["benchmark_files"] = benchmark_files
        if sample.setup is not None:
            # carry the source as-is (a host path anchored for relative values,
            # else literal text or a data URI); run_benchmark_setup resolves it
            # through inspect's own read_sandboxenv_file, which handles file /
            # data-uri / http -- pre-reading the text here dropped the latter two
            metadata["benchmark_setup"] = _anchored(sample.setup, run_dir)

    return Sample(
        id=str(item.sample_id),
        input=prompt,
        target=sample.target,
        metadata=metadata,
        sandbox=sandbox,
        files=files,
    )


def _anchored(value: str, run_dir: Path) -> str:
    # relative file values resolve against the audited task's directory, not ours
    if not value.startswith("/") and (run_dir / value).is_file():
        return str(run_dir / value)
    return value


def item_files(
    task: Task,
    sample: Sample,
    attempts: list[AttemptRef],
    *,
    stage: Path,
    original_env: SandboxEnvironmentType | None = None,
    redact: Collection[str] = ANSWER_METADATA,
    contract: SolverContract | None = None,
) -> dict[str, str]:
    """Stage one item's files on the host and return its `Sample.files` mapping.

    Args:
        task: The task being audited.
        sample: The sample being audited.
        attempts: The recorded attempts at this sample.
        stage: Directory to stage this item's files in.
        original_env: The audited task's own sandbox definition, staged verbatim.
        redact: Metadata keys stripped from the staged `sample.json`.
        contract: The audited task's declared tool surface, diffed against the
            sliced logs into `discrepancies.md`.
    """
    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    # values must be host paths: inspect resolves a contents-shaped value as a data
    # uri, then a url, then a file at that path
    def staged(name: str, content: str) -> None:
        host = stage / name
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_text(content, encoding="utf-8")
        files[f"{AUDIT_ROOT}/{name}"] = str(host)

    # the sample in inspect's own shape, one-record dataset -- with answer-bearing
    # metadata redacted (SWE-bench keeps its gold patch and hidden tests here), so the
    # auditor cannot read the benchmark's own solution and launder it as a finding.
    # the grader still has the full answer (carried separately as `benchmark_metadata`).
    record = sample.model_dump(exclude_none=True, exclude={"files", "sandbox", "setup"})
    if isinstance(record.get("metadata"), dict):
        for key in redact:
            record["metadata"].pop(key, None)

    # an item's media is referenced from its content by HOST path, which means nothing
    # inside the container. copy each file in and rewrite the reference, so an image
    # item is auditable at all -- otherwise the auditor is asked to judge an image
    # against a path that does not resolve.
    files.update(media_files(record, stage=stage / "media"))
    staged("sample.json", json.dumps([record], indent=2, default=str))

    # where grading lives and how to read it
    staged("gold/grading.md", grading_doc(task, sample, redact=redact))

    # the benchmark's own code, so "read the real grader" is a thing the auditor can
    # actually do. `task_requirements` only pins DISTRIBUTIONS, so a benchmark that is
    # a loose repo rather than a package (most of them) left the auditor with a
    # grading.md pointing at modules it could not import.
    files.update(benchmark_files(task, stage=stage / "benchmark"))

    # the environment's own definition and the sliced logs
    files.update(env_files(original_env, stage=stage / "env"))
    logs, logged = sample_logs(attempts, stage=stage / "logs")
    files.update(logs)

    # declared vs recorded tools, diffed mechanically before any model reasons.
    # a clean diff still stages: the absence of discrepancies is a checked claim.
    # `logged` is computed from the in-memory samples in sample_logs -- no re-read.
    if contract is not None and logs:
        doc = discrepancies_doc(contract, logged)
        if doc is not None:
            staged("discrepancies.md", doc)
    return files


def sample_logs(
    attempts: list[AttemptRef], *, stage: Path
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Write one real `.eval` per source log, sliced to this item's attempts.

    The header is kept as the source wrote it (task, model, scorers, config,
    packages, plan) so the auditor checks how attempts were graded and elicited
    against the log itself rather than a summary of ours -- with one field
    narrowed: `eval.dataset.sample_ids`/`samples` are cut to the slice, or
    inspect's streaming reader would walk the whole original id list and raise.

    Returns `(files, tools)`: the staged path map, and per sliced log the set of
    tool names its attempts show reaching the model (union over `ModelEvent`s).
    The tool names come from the samples already in memory here -- no re-read.
    """
    if not attempts:
        return {}, {}

    by_log: dict[str, list[AttemptRef]] = defaultdict(list)
    for attempt in attempts:
        by_log[attempt.log_file].append(attempt)

    # Reserve original names before allocating suffixes, including names from
    # sources visited later. An eval_id identifies an evaluation, not a file:
    # copies and rescored versions can share it and must not overwrite each other.
    source_names = {
        source: Path(source.replace("file://", "")).name for source in by_log
    }
    reserved_names = set(source_names.values())

    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    tools: dict[str, set[str]] = {}
    for log_file, refs in sorted(by_log.items()):
        # read the header and just this item's samples -- one shared reader for the
        # subset, not a read_eval_log_sample call (and central-directory parse) each
        try:
            log = read_eval_log(log_file, header_only=True)
            log.samples = read_eval_log_samples_by_id(
                log_file,
                [(ref.sample_id, ref.epoch) for ref in sorted(refs, key=lambda r: r.epoch)],
            )
        except Exception as ex:
            # a missing log costs the auditor one log's attempts, not the whole item
            logger.warning(
                "could not slice %s for sample %s: %s: %s",
                log_file, refs[0].sample_id, type(ex).__name__, ex,
            )
            continue

        # narrow the header's dataset ids to what the slice actually holds:
        # inspect's streaming reader iterates header ids x epochs, so a
        # verbatim id list makes `read_eval_log_samples` (the call our own
        # reading-logs skill teaches) raise IndexError on every absent sample.
        # everything else in the header stays verbatim.
        sliced_ids = list(dict.fromkeys(s.id for s in log.samples))
        log.eval.dataset.sample_ids = sliced_ids
        log.eval.dataset.samples = len(sliced_ids)

        # Keep the source filename where possible; disambiguate colliding files
        # without changing their Inspect headers or losing a recorded attempt.
        name = source_names[log_file]
        host = stage / name
        if name in tools:
            suffix = 2
            while True:
                host = stage / f"{Path(name).stem}-{suffix}{Path(name).suffix}"
                if host.name not in reserved_names and host.name not in tools:
                    break
                suffix += 1
        write_eval_log(log, str(host))
        files[f"{AUDIT_ROOT}/logs/{host.name}"] = str(host)
        tools[host.name] = {
            tool.name
            for sample in log.samples
            for event in _solver_events(sample.events)
            if isinstance(event, ModelEvent)
            for tool in event.tools
        }

    return files, tools


def _solver_events(events: list[Event]) -> list[Event]:
    """The sample's events minus everything that happened under scoring.

    A model-graded scorer makes its own model calls (an extractor with a `submit`
    tool, a judge with a rubric), and those land in the sample's events like any
    other -- so read naively they show tools "reaching the model" that the
    evaluated model never had. Everything under a `scorers`-type span is the
    grader's, not the agent's.

    `event_tree` does the parent linkage: an earlier version tracked span ids and
    parents by hand and broke the first time inspect nested them differently.
    """
    def outside_scoring(node: EventTreeNode) -> list[Event]:
        if isinstance(node, EventTreeSpan):
            if node.type in ("scorers", "scorer"):
                return []
            return [
                node.begin,
                *[e for child in node.children for e in outside_scoring(child)],
                *([node.end] if node.end else []),
            ]
        return [node]

    return [
        event for node in event_tree(events) for event in outside_scoring(node)
    ]


def benchmark_source_files(task: Task) -> dict[str, Path]:
    """The benchmark's own Python sources: its scorers, and the task's own directory.

    Returns `{name in benchmark/: host path}`. Data and logs are excluded: the auditor
    reads the item from `sample.json` and the attempts from `logs/`, and a benchmark's
    data directory is routinely large enough to swamp the cell.
    """
    found: dict[str, Path] = {}

    def take(path: str | None, name: str | None = None) -> None:
        if not path:
            return
        source = Path(path)
        if source.is_file() and source.suffix == ".py":
            found.setdefault(name or source.name, source)

    # every scorer's defining module -- this is the grader itself
    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    for scorer in scorers:
        if scorer is None:
            continue
        fn = getattr(scorer, "__wrapped__", scorer)
        module = getattr(fn, "__module__", None)
        if not module:
            continue
        try:
            take(_inspect.getsourcefile(importlib.import_module(module)))
        except Exception:  # a module we cannot import is not worth failing the cell
            logger.debug("could not locate source for scorer module %s", module)

    # the task's own directory: its task definition, its grade(), its helpers
    try:
        run_dir = Path(task_run_dir(task))
    except Exception:
        return found
    if run_dir.is_dir():
        for source in sorted(run_dir.glob("*.py")):
            take(str(source))
    return found


def benchmark_files(task: Task, *, stage: Path) -> dict[str, str]:
    """Stage the benchmark's own code under `benchmark/` for the auditor to read."""
    sources = benchmark_source_files(task)
    if not sources:
        return {}
    stage.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for name, source in sources.items():
        host = stage / name
        host.write_bytes(source.read_bytes())
        files[f"{AUDIT_ROOT}/benchmark/{name}"] = str(host)
    return files


MEDIA_ROOT = "media"
_MEDIA_KEYS = ("image", "audio", "video", "document")


def media_files(record: dict[str, Any], *, stage: Path) -> dict[str, str]:
    """Copy an item's media into the cell and rewrite its references, in place.

    Inspect content carries media as `{"type": "image", "image": <path-or-uri>}`. A
    local path is resolved against the host that built the dataset, so it is dead
    inside the auditor's container; a `data:` or `http` URI needs no staging.

    Args:
        record: The serialised sample, mutated so its media points into the cell.
        stage: Directory to copy the media into.

    Returns:
        The `Sample.files` entries for the copied media.
    """
    files: dict[str, str] = {}
    seen: dict[str, str] = {}
    used: set[str] = set()

    def rewrite(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                rewrite(child)
            return
        if not isinstance(node, dict):
            return
        for key in _MEDIA_KEYS:
            value = node.get(key)
            if not isinstance(value, str) or "://" in value or value.startswith("data:"):
                continue
            source = Path(value)
            if not source.is_file():
                logger.warning("item media not found on the host, not staged: %s", value)
                continue
            if value not in seen:
                stage.mkdir(parents=True, exist_ok=True)
                # keep the parent directory in the name: basenames repeat across
                # items -- and uniquify beyond that, or two distinct files
                # sharing parent+basename collapse into one and the auditor is
                # shown the wrong picture with no error anywhere
                name = f"{source.parent.name}_{source.name}" if source.parent.name else source.name
                n = 1
                while name in used:
                    name = f"{n}_{source.parent.name}_{source.name}"
                    n += 1
                used.add(name)
                host = stage / name
                host.write_bytes(source.read_bytes())
                seen[value] = f"{AUDIT_ROOT}/{MEDIA_ROOT}/{name}"
                files[seen[value]] = str(host)
            node[key] = seen[value]
        for child in node.values():
            rewrite(child)

    rewrite(record.get("input"))
    return files


def env_files(spec: SandboxEnvironmentType | None, *, stage: Path) -> dict[str, str]:
    """Stage the environment's own definition for the auditor to read."""
    resolved = resolve_sandbox_environment(spec)
    if resolved is None or resolved.config is None:
        return {}

    stage.mkdir(parents=True, exist_ok=True)
    if isinstance(resolved.config, str):
        source = Path(resolved.config)
        if not source.is_file():
            return {}
        host = stage / source.name
        host.write_bytes(source.read_bytes())
    else:
        # a config object rather than a file (some sandbox providers configure inline)
        host = stage / "sandbox.json"
        host.write_text(resolved.config.model_dump_json(indent=2), encoding="utf-8")

    return {f"{AUDIT_ROOT}/env/{host.name}": str(host)}


def grading_doc(
    task: Task, sample: Sample, *, redact: Collection[str] = ANSWER_METADATA
) -> str:
    """Render `gold/grading.md` for one item.

    Args:
        task: The task being audited.
        sample: The sample being audited.
        redact: Metadata keys withheld from the auditor. Their names are withheld
            too: naming a key can pre-empt a verdict as surely as its value.
    """
    # each scorer as (qualified name, module); the module is how the auditor finds the code
    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    named: list[tuple[str, str]] = []
    for scorer in scorers:
        if scorer is None:
            continue
        fn = getattr(scorer, "__wrapped__", scorer)
        named.append(
            (
                getattr(fn, "__qualname__", getattr(fn, "__name__", "?")),
                getattr(fn, "__module__", "?"),
            )
        )

    return resource(str(GRADING_TEMPLATE), type="file").format(
        scorers="\n".join(f"- `{name}`, defined in `{module}`" for name, module in named)
        or "- not recovered",
        modules=" ".join(sorted({module for _, module in named})) or "?",
        metadata_keys=(
            ", ".join(
                f"`{k}`" for k in sorted((sample.metadata or {}).keys()) if k not in redact
            )
            or "(none)"
        ),
    )
