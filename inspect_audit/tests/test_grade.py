"""The grader judges the benchmark's world, never the audit's.

Regression suite for the state handed to the benchmark's scorers: before
`benchmark_task_state`, `grade` copied the *auditor's* TaskState, so a scorer
reading `input` got the audit prompt as the question, one reading `messages`
got the auditor's react transcript, `choices` came up empty, and `store` was
the audit's. Every test here fails against that construction.
"""

import json

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ModelName
from inspect_ai.scorer import Score, Scorer, Target, match, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import Store, store_as

from inspect_audit import audit_task
from inspect_audit._agent import grade_benchmark
from inspect_audit._audit import ITEM_PROMPT
from inspect_audit._state import (
    BenchmarkState,
    append_message,
    benchmark_task_state,
    complete_attempt,
    seed_new,
)

QUESTION = "In what year did the Battle of Hastings take place?"


def audit_state(metadata: dict[str, object]) -> TaskState:
    """A TaskState shaped like the auditor's own, mid-audit."""
    return TaskState(
        model=ModelName("mockllm/model"),
        sample_id="42",
        epoch=1,
        input=ITEM_PROMPT,
        messages=[],
        target=Target("1066"),
        metadata={
            "audit_item": {"task": "fixture_task", "sample_id": "42"},
            "benchmark_metadata": {"category": "maths"},
            "benchmark_input": QUESTION,
            "benchmark_choices": None,
            **metadata,
        },
    )


def make_session() -> BenchmarkState:
    return BenchmarkState(store=Store())


def test_the_grader_sees_the_benchmark_question_not_the_audit_prompt() -> None:
    graded = benchmark_task_state(audit_state({}), make_session(), "1066")

    assert graded.input_text == QUESTION
    assert ITEM_PROMPT not in graded.input_text


def test_the_grader_sees_the_benchmark_choices() -> None:
    current = audit_state({"benchmark_choices": ["1056", "1066", "1076"]})
    graded = benchmark_task_state(current, make_session(), "1066")

    assert [choice.value for choice in graded.choices] == ["1056", "1066", "1076"]


def test_the_grader_sees_the_session_not_the_audit_transcript() -> None:
    session = make_session()
    seed_new(session, QUESTION, prompt=None)
    append_message(session, "assistant", "It was 1066.")

    graded = benchmark_task_state(audit_state({}), session, "1066")

    assert [m.text for m in graded.messages] == [QUESTION, "It was 1066."]


def test_the_grader_sees_benchmark_metadata_only() -> None:
    graded = benchmark_task_state(audit_state({}), make_session(), "")

    assert graded.metadata == {"category": "maths"}
    assert "audit_item" not in graded.metadata


def test_the_answer_is_the_completion_and_bare_grade_is_empty() -> None:
    current = audit_state({})
    assert (
        benchmark_task_state(current, make_session(), "1066").output.completion
        == "1066"
    )
    assert benchmark_task_state(current, make_session(), "").output.completion == ""


def test_the_grader_sees_the_attempt_store() -> None:
    session = make_session()
    session.attempt_store = {"scaffold_answer": "1066"}
    graded = benchmark_task_state(audit_state({}), session, "")

    assert graded.store.get("scaffold_answer") == "1066"


@scorer(metrics=[])
def world_probe() -> Scorer:
    """Records what a scorer actually sees, so the eval below can assert on it."""

    async def score(state: TaskState, target: Target) -> Score:
        return Score(
            value="C",
            answer=state.input_text,
            explanation=json.dumps(
                {
                    "messages": [m.text for m in state.messages],
                    "metadata_keys": sorted(state.metadata or {}),
                    "completion": state.output.completion,
                }
            ),
        )

    return score


def test_grade_tool_end_to_end_hands_the_scorer_the_benchmark_world() -> None:
    """The full path: audit task -> solver context -> grade tool -> real scorer."""
    seen: dict[str, object] = {}

    @solver
    def probing_auditor() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            session = store_as(BenchmarkState)
            seed_new(session, QUESTION, prompt=None)
            complete_attempt(session, "It was 1066.")
            grade = grade_benchmark([world_probe()])
            seen.update(json.loads(await grade(answer="1066")))
            return state

        return solve

    target = Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=42, input=QUESTION, target="1066")]),
        scorer=match(),
    )
    audit = audit_task(target, sandbox="local", solver=probing_auditor())
    # the local sandbox would write the cell to literal /audit on the host;
    # this test is about the graded state, not staging (docker e2e covers that)
    for audit_sample in audit.dataset:
        audit_sample.files = None
    logs = eval(audit, model="mockllm/model", display="none")
    assert logs[0].status == "success"

    scores = seen["scores"]
    assert isinstance(scores, dict)
    # the question, not the audit prompt
    assert scores["answer"] == QUESTION
    world = json.loads(str(scores["explanation"]))
    # the reconstructed session, not the auditor's transcript
    assert world["messages"] == [QUESTION, "It was 1066."]
    # the benchmark's metadata, not the audit's bookkeeping
    assert "audit_item" not in world["metadata_keys"]
    assert world["completion"] == "1066"
    # and the receipt says how synthetic the graded session was
    graded = seen["graded"]
    assert isinstance(graded, dict)
    assert graded["provenance"] == {"real": 1, "authored": 1}


@pytest.mark.parametrize("wrapped", [False, True])
def test_a_grader_that_cannot_run_is_a_tool_error_not_a_sample_error(
    wrapped: bool,
) -> None:
    """The judge model being gone reaches the auditor as a tool error; the sample survives."""
    from inspect_ai.scorer import accuracy
    from inspect_ai.tool import ToolError

    @scorer(metrics=[accuracy()])
    def exploding() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            try:
                raise RuntimeError("No endpoints found for google/gemini-2.0-flash-001")
            except RuntimeError as cause:
                if wrapped:
                    raise ValueError("Request: " + "prompt " * 1000) from cause
                raise

        return score

    seen: dict[str, object] = {}

    @solver
    def probing_auditor() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            session = store_as(BenchmarkState)
            seed_new(session, QUESTION, prompt=None)
            complete_attempt(session, "MOVE: a1a2")
            grade = grade_benchmark([exploding()])
            try:
                await grade(answer="MOVE: a1a2")
            except ToolError as ex:
                seen["error"] = ex.message
            return state

        return solve

    target = Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=42, input=QUESTION, target="1066")]),
        scorer=match(),
    )
    audit = audit_task(target, sandbox="local", solver=probing_auditor())
    for audit_sample in audit.dataset:
        audit_sample.files = None
    logs = eval(audit, model="mockllm/model", display="none")
    assert logs[0].status == "success"
    assert "grader failed to run" in str(seen["error"])
    assert "No endpoints found" in str(seen["error"])
