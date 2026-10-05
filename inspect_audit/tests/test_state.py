"""The reconstructed benchmark session and its provenance discipline.

Exercises the pure state operations against a real recorded attempt (a fixture
eval run under mockllm), without a sandbox: the `attempt` tool is a thin
dispatcher over these, plus the mirror write tested end-to-end under Docker.
"""

import pytest
from inspect_ai.log import read_eval_log_samples
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.util import Store
from test_helpers.logs import run_fixture_eval

from inspect_audit._state import (
    BenchmarkState,
    append_message,
    complete_attempt,
    edit_message,
    seed_from_sample,
    seed_new,
    truncate_messages,
)


def make_state() -> BenchmarkState:
    return BenchmarkState(store=Store())


def test_seed_new_marks_input_real_and_prompt_authored() -> None:
    state = make_state()
    seed_new(state, "What year?", prompt="You are an agent.")

    assert [m.provenance for m in state.messages] == ["authored", "real"]
    assert state.messages[0].message.role == "system"
    assert state.messages[1].message.text == "What year?"
    assert state.seeded == "new"
    assert not state.completed


def test_seed_new_without_prompt_is_input_only() -> None:
    state = make_state()
    seed_new(state, "What year?", prompt=None)

    assert state.provenance_mix() == {"real": 1}


def test_seed_from_recorded_attempt_is_verbatim_and_real(tmp_path: object) -> None:
    log = run_fixture_eval(str(tmp_path))
    sample = next(read_eval_log_samples(log, all_samples_required=False))

    state = make_state()
    seed_from_sample(state, sample, source="run.eval#epoch=1")

    assert state.provenance_mix() == {"real": len(sample.messages)}
    assert state.chat_messages() == sample.messages
    assert state.output == sample.output
    assert state.completed
    assert state.seeded == "run.eval#epoch=1"


def test_append_assistant_with_tool_calls() -> None:
    state = make_state()
    append_message(
        state,
        "assistant",
        "running it",
        tool_calls='[{"id": "1", "function": "bash", "arguments": {"command": "ls"}}]',
    )

    message = state.messages[0].message
    assert isinstance(message, ChatMessageAssistant)
    assert message.tool_calls is not None and message.tool_calls[0].function == "bash"
    assert state.messages[0].provenance == "authored"


def test_append_rejects_malformed_input() -> None:
    state = make_state()
    with pytest.raises(ValueError, match="tool_calls"):
        append_message(state, "assistant", "x", tool_calls="not json")
    with pytest.raises(ValueError, match="tool_call_id"):
        append_message(state, "tool", "output")
    with pytest.raises(ValueError, match="unknown role"):
        append_message(state, "oracle", "x")


def test_edit_marks_the_message_authored() -> None:
    state = make_state()
    seed_new(state, "What year?", prompt=None)
    edit_message(state, 0, "What decade?")

    assert state.messages[0].provenance == "authored"
    assert state.messages[0].message.text == "What decade?"
    with pytest.raises(ValueError, match="out of range"):
        edit_message(state, 5, "x")


def test_truncate_reopens_the_attempt() -> None:
    state = make_state()
    seed_new(state, "What year?", prompt=None)
    complete_attempt(state, "1066")
    assert state.completed

    truncate_messages(state, 1)
    assert len(state.messages) == 1
    assert not state.completed
    assert state.output is None


def test_complete_sets_output_and_transcript() -> None:
    state = make_state()
    seed_new(state, "What year?", prompt=None)
    complete_attempt(state, "1066")

    assert state.completed
    assert state.output is not None and state.output.completion == "1066"
    # the answer also lands as an assistant turn, so transcript-readers see
    # what a submitting agent's transcript shows
    assert state.messages[-1].message.role == "assistant"
    assert state.messages[-1].provenance == "authored"


def test_complete_empty_answer_grades_the_box() -> None:
    state = make_state()
    seed_new(state, "Fix the bug.", prompt=None)
    complete_attempt(state, "")

    assert state.completed
    assert state.output is not None and state.output.completion == ""
    # no phantom assistant turn for an empty submission
    assert state.messages[-1].message.role == "user"
