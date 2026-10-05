from pathlib import Path
from typing import Any

from inspect_ai import Task

# private core import, the same coupling scout takes on inspect_ai internals
from inspect_ai._eval.loader import load_tasks
from inspect_ai.log import EvalLog, read_eval_log


def resolve_task(spec: str | Task, task_args: dict[str, Any] | None = None) -> Task:
    """Resolve a task spec to a `Task`.

    Args:
        spec: What `inspect eval` accepts -- a registry name, `file.py@name`, a
            directory -- or a `Task` to pass through.
        task_args: Task arguments the task was run with.
    """
    if isinstance(spec, Task):
        return spec

    # a registry name resolves with or without its package prefix depending on
    # how the package was installed: a wheel/git install registers `pkg/name`,
    # an editable install (not recognised as a package by inspect) registers the
    # bare `name`. try what was given, then the other form, so a user need not
    # know which install they have.
    candidates = [spec]
    if "/" in spec and "@" not in spec and not spec.endswith(".py"):
        candidates.append(spec.split("/", 1)[1])
    elif "/" not in spec and "@" not in spec and not spec.endswith(".py"):
        candidates.extend(f"{pkg}/{spec}" for pkg in _registry_packages())

    tried: list[str] = []
    for candidate in candidates:
        try:
            tasks = load_tasks([candidate], task_args or {})
        except Exception as ex:
            tried.append(f"  {candidate!r}: {type(ex).__name__}: {ex}")
            continue
        if len(tasks) == 1:
            return tasks[0]
        tried.append(f"  {candidate!r}: resolved to {len(tasks)} tasks")
    raise ValueError(
        f"Task spec {spec!r} did not resolve to exactly one task. Tried:\n"
        + "\n".join(tried)
        + "\nAddress a single task, e.g. 'pkg/task_name' or 'file.py@task_name'."
    )


def _registry_packages() -> list[str]:
    """Packages that register inspect tasks via the `inspect_ai` entry point."""
    from importlib.metadata import entry_points

    return sorted({ep.name for ep in entry_points(group="inspect_ai")})


def resolve_task_from_log(log: str | Path | EvalLog) -> Task:
    """Resolve the task that produced a log, with the arguments it was run with."""
    header = log if isinstance(log, EvalLog) else read_eval_log(str(log), header_only=True)
    spec = header.eval
    args = spec.task_args or {}

    # prefer the file the eval was run from, fall back to the registry name: a log
    # routinely outlives the file's location
    candidates: list[str] = []
    if spec.task_file and spec.task_registry_name:
        candidates.append(f"{spec.task_file}@{spec.task_registry_name}")
    if spec.task_registry_name:
        candidates.append(spec.task_registry_name)
    if spec.task:
        candidates.append(spec.task)

    errors: list[str] = []
    for candidate in candidates:
        try:
            return resolve_task(candidate, args)
        except Exception as ex:
            errors.append(f"  {candidate!r}: {type(ex).__name__}: {ex}")

    raise ValueError(
        "Could not resolve the task that produced this log. Tried:\n"
        + "\n".join(errors)
        + "\nPass the task explicitly, or run from the directory the eval was run from."
    )
