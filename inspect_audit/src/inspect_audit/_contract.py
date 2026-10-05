"""Recover the interaction contract a benchmark declares.

A task's tools and prompt are not attributes: they live inside solver closures,
set by `use_tools(...)` or captured by agent scaffolds like `react(...)`. There
is nothing to parse -- but the registry records the constructor parameters of
every decorated object, with nested tools serialised as reconstructible
references. Walking those records recovers the declared tool surface without
executing anything, the way `resolve_scorers` rebuilds scorers from a log.

The registry records *construction*, so a solver that assembles tools inside
`solve()` is opaque here; `SolverContract.complete` says whether the
enumeration can be trusted. What actually reached the model during recorded
attempts is in the logs (`ModelEvent.tools`), and `discrepancies_doc` renders
the diff between the two -- declared-now versus ran-then -- which is itself
evidence: drift, dynamism, or an undeclared affordance.
"""

from dataclasses import dataclass, field
from logging import getLogger
from typing import Any, cast

from inspect_ai import Task

# `registry_create` and `registry_info` are public; `registry_params` and
# `is_registry_object` have no public equivalent, which is the one thing this module
# needs from the private surface
from inspect_ai._util.registry import is_registry_object, registry_params
from inspect_ai.tool import Tool, ToolDef
from inspect_ai.util import registry_create, registry_info

logger = getLogger(__name__)


@dataclass
class SolverContract:
    """The interaction surface a solver declares via the registry."""

    tools: list[ToolDef] = field(default_factory=list)
    """Declared tools, rebuilt live -- schema and implementation."""

    prompt: str | None = None
    """The declared prompt template, where one was recoverable."""

    unrecovered: list[str] = field(default_factory=list)
    """Tools declared by name that could not be rebuilt from the registry."""

    complete: bool = True
    """Whether the enumeration is trustworthy: `False` means part of the
    solver is opaque to the registry, so tools may exist beyond `tools`."""

    def tool_names(self) -> set[str]:
        """Every declared tool name, rebuilt or not."""
        return {tool.name for tool in self.tools} | set(self.unrecovered)


def task_contract(task: Task) -> SolverContract:
    """The contract `task` declares: its setup's tools and its solver's.

    Args:
        task: The task whose declared interaction surface to recover.
    """
    contract = SolverContract()
    for solver in (task.setup, task.solver):
        if solver is not None:
            _walk_object(solver, contract, depth=0)
    return contract


def solver_contract(solver: object) -> SolverContract:
    """The contract one solver (or agent, or list of solvers) declares."""
    contract = SolverContract()
    _walk_object(solver, contract, depth=0)
    return contract


# recursion bound: registry params are finite serialised data, but a cycle in a
# hand-built structure should degrade to incomplete rather than recurse forever
_MAX_DEPTH = 16


def _walk_object(value: object, contract: SolverContract, depth: int) -> None:
    """Walk a live object: a tool is taken, anything else registered recurses."""
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_object(item, contract, depth + 1)
        return
    if not is_registry_object(value):
        logger.warning(
            f"solver component {value!r} is not a registry object; "
            "its tools (if any) cannot be recovered"
        )
        contract.complete = False
        return
    if registry_info(value).type == "tool":
        contract.tools.append(ToolDef(cast(Tool, value)))
        return
    _walk_params(registry_params(value), contract, depth + 1)


def _walk_params(params: dict[str, Any], contract: SolverContract, depth: int) -> None:
    if "prompt" in params and contract.prompt is None:
        contract.prompt = _as_prompt(params["prompt"])
    for value in params.values():
        _walk_value(value, contract, depth + 1)


def _walk_value(value: Any, contract: SolverContract, depth: int) -> None:
    """Walk a serialised registry param: refs rebuild or recurse, in place."""
    if depth > _MAX_DEPTH:
        logger.warning("registry walk exceeded maximum depth; contract incomplete")
        contract.complete = False
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_value(item, contract, depth + 1)
        return
    if not isinstance(value, dict):
        return
    ref = _registry_ref(value)
    if ref is None:
        for item in value.values():
            _walk_value(item, contract, depth + 1)
        return
    kind, name, params = ref
    if kind == "tool":
        try:
            contract.tools.append(ToolDef(registry_create("tool", name, **params)))
        except Exception as ex:  # named but unbuildable: keep the name for the diff
            logger.warning(f"could not rebuild declared tool {name!r}: {ex}")
            contract.unrecovered.append(name)
    elif kind in ("solver", "agent"):
        # the ref carries its params inline, so recurse without constructing
        _walk_params(params, contract, depth + 1)
    else:
        for item in params.values():
            _walk_value(item, contract, depth + 1)


def _registry_ref(value: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """A serialised registry reference, as `(type, name, params)`."""
    if (
        {"type", "name", "params"} <= value.keys()
        and isinstance(value["type"], str)
        and isinstance(value["name"], str)
        and isinstance(value["params"], dict)
    ):
        return value["type"], value["name"], dict(value["params"])
    return None


def _as_prompt(value: Any) -> str | None:
    # a str prompt is itself; an AgentPrompt serialises positionally, with the
    # operator's instructions first and scaffold boilerplate after
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
        return value[0]
    logger.debug(f"prompt param of unrecognised shape not recovered: {type(value)}")
    return None


def discrepancies_doc(
    contract: SolverContract, logged: dict[str, set[str]]
) -> str | None:
    """Render the declared-versus-ran tool diff for one item, or `None`.

    Args:
        contract: What the audited task declares, per the registry.
        logged: Tool names observed in each sliced log, keyed by log filename.

    Returns:
        A markdown document when there is anything to diff, else `None`.
        An empty diff still renders: the absence of discrepancies is itself
        a checked claim, not a default.
    """
    if not logged:
        return None

    declared = contract.tool_names()
    any_logged = any(logged.values())

    # a complete-looking contract that recovered NO tools while the logs show
    # some is far likelier to be a failed walk (a registry-shape change we did
    # not parse) than a benchmark that truly declares nothing. listing every
    # observed tool as "undeclared" would be our bug filed as the benchmark's.
    walk_failed = contract.complete and not declared and any_logged

    lines = [
        "# Declared vs recorded tools",
        "",
        "What the benchmark's current definition declares, against what each "
        "sliced log shows actually reaching the evaluated model. Produced "
        "mechanically; confirm anything surprising against the log itself.",
        "",
        f"Declared: {_named(declared)}",
    ]
    if contract.unrecovered:
        lines.append(
            f"Declared but not rebuildable from the registry: {_named(set(contract.unrecovered))}"
        )
    if not contract.complete:
        lines.append(
            "NOTE: part of the solver is opaque to the registry, so the declared "
            "list may be incomplete. Treat the logs as the authority on tools."
        )
    elif walk_failed:
        lines.append(
            "NOTE: the registry walk recovered no tools while the logs show some -- "
            "the walk likely failed rather than the benchmark declaring none. "
            "Treating the logs as the authority on tools."
        )

    clean = True
    for log_name in sorted(logged):
        observed = logged[log_name]
        lines += ["", f"## {log_name}", "", f"Observed: {_named(observed)}"]
        undeclared = observed - declared
        unobserved = declared - observed
        # observed-but-not-declared is informative even for a known-incomplete
        # contract (dynamic tools), but a failed walk has nothing to declare
        # against, so it would list every observed tool as undeclared -- suppress.
        if undeclared and not walk_failed:
            clean = False
            lines.append(f"- observed but not declared: {_named(undeclared)}")
        # only a complete walk can honestly claim a declared tool never ran
        if unobserved and contract.complete and not walk_failed:
            clean = False
            lines.append(f"- declared but never reached the model: {_named(unobserved)}")
    if clean:
        lines += ["", "No discrepancies found."]
    return "\n".join(lines) + "\n"


def _named(names: set[str]) -> str:
    return ", ".join(f"`{name}`" for name in sorted(names)) or "(none)"
