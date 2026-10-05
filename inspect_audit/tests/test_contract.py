"""Recovering the declared tool surface from the registry.

The registry records how every decorated object was constructed, so a task's
tools are recoverable without executing anything -- unless the solver builds
them at runtime, which the contract must admit rather than guess.
"""

from inspect_ai import Task
from inspect_ai.agent import react
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match
from inspect_ai.solver import Generate, Solver, TaskState, generate, solver, use_tools
from inspect_ai.tool import ToolDef, bash, python

from inspect_audit._contract import (
    SolverContract,
    discrepancies_doc,
    solver_contract,
    task_contract,
)


def make_task(solver: object) -> Task:
    return Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=1, input="q", target="a")]),
        solver=solver,  # type: ignore[arg-type]
        scorer=match(),
    )


def test_react_task_declares_tools_and_prompt() -> None:
    task = make_task(react(tools=[bash(timeout=60), python()], prompt="Do the work."))
    contract = task_contract(task)

    assert contract.complete
    assert contract.tool_names() == {"bash", "python"}
    assert contract.prompt == "Do the work."


def test_declared_tool_params_survive_the_round_trip() -> None:
    contract = solver_contract(react(tools=[bash(timeout=60)]))

    (tool,) = contract.tools
    assert tool.name == "bash"
    # the rebuilt tool carries the real schema, not a name-only stub
    assert "command" in tool.parameters.properties


def test_use_tools_chain_declares_tools() -> None:
    task = make_task([use_tools([bash(timeout=30)]), generate()])
    contract = task_contract(task)

    assert contract.complete
    assert contract.tool_names() == {"bash"}


def test_setup_solver_tools_are_included() -> None:
    task = Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=1, input="q", target="a")]),
        setup=use_tools([python()]),
        solver=[use_tools([bash()]), generate()],
        scorer=match(),
    )
    assert task_contract(task).tool_names() == {"bash", "python"}


def test_undecorated_solver_is_incomplete_not_wrong() -> None:
    async def opaque(state: TaskState, generate: Generate) -> TaskState:
        return state

    contract = solver_contract(opaque)

    assert not contract.complete
    assert contract.tools == []


def test_dynamic_tools_inside_solve_are_invisible_and_admitted() -> None:
    # tools assembled at runtime never reach the registry record; the contract
    # for the *registered* solver is complete but empty, which is correct: the
    # declaration really is empty, and the logs are the authority on what ran
    @solver
    def dynamic() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.tools = [bash()]
            return state

        return solve

    contract = solver_contract(dynamic())
    assert contract.tools == []
    assert contract.complete


def test_rewrapped_tool_is_named_even_when_not_rebuildable() -> None:
    renamed = ToolDef(bash(timeout=9), name="renamed_bash", description="renamed").as_tool()
    contract = solver_contract(react(tools=[renamed, python()]))

    assert "python" in {tool.name for tool in contract.tools}
    # the rename is registry-visible by name whether or not it rebuilds
    assert "renamed_bash" in contract.tool_names()


def test_discrepancies_doc_reports_both_directions() -> None:
    contract = solver_contract(react(tools=[bash(), python()]))
    doc = discrepancies_doc(contract, {"run.eval": {"bash", "web_search"}})

    assert doc is not None
    assert "observed but not declared: `web_search`" in doc
    assert "declared but never reached the model: `python`" in doc


def test_discrepancies_doc_clean_diff_still_renders() -> None:
    contract = solver_contract(react(tools=[bash()]))
    doc = discrepancies_doc(contract, {"run.eval": {"bash"}})

    assert doc is not None
    assert "No discrepancies found." in doc


def test_discrepancies_doc_without_logs_is_none() -> None:
    assert discrepancies_doc(SolverContract(), {}) is None


def test_audit_cell_stages_the_diff_against_real_logs(tmp_path: object) -> None:
    from pathlib import Path

    from test_helpers.logs import run_fixture_eval

    from inspect_audit import audit_task

    logs = str(tmp_path)
    run_fixture_eval(logs)

    # the audited task declares bash; the recorded attempts ran without tools
    audit = audit_task(make_task(react(tools=[bash()])), logs, samples=["1"])
    files = audit.dataset[0].files or {}

    staged = files.get("/audit/discrepancies.md")
    assert staged is not None
    doc = Path(staged).read_text()
    assert "declared but never reached the model: `bash`" in doc


def test_incomplete_contract_does_not_accuse_the_logs() -> None:
    # an opaque solver cannot claim "declared but never ran": the declaration
    # itself is untrustworthy, so only the observed side may be asserted
    contract = SolverContract(tools=[ToolDef(bash())], complete=False)
    doc = discrepancies_doc(contract, {"run.eval": {"bash", "web_search"}})

    assert doc is not None
    assert "observed but not declared" in doc
    assert "never reached the model" not in doc
