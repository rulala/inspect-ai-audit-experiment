"""The benchmark's own session, reconstructed for the auditor to handle.

An audit reconstructs every layer of the original eval -- its environment, its
metadata, its grader -- except the session itself. This module adds that
counterpart: a `BenchmarkState` held in the typed store, representing the
evaluated agent's conversation as an object the auditor manipulates through
the `attempt` tool and the grader consumes. The auditor lives in its own
`TaskState`; it only ever *handles* this one. The invariant enforced in
`_agent.grade_benchmark` is the mirror of the sandbox isolation: the
benchmark's scorer sees the benchmark's state, never the audit's.

Every message carries provenance, because verdict strength reads off it:

- `real`     -- loaded verbatim from a recorded attempt, or the item's own input
- `enacted`  -- produced by actually executing against the benchmark box
- `authored` -- written by the auditor, including elicitation reconstructed
                from the declared prompt template (an approximation, so it
                does not get to claim `real`)

Assistant turns may be authored freely: a real model could have said anything,
so authored assistant content explores only the grader's reachable domain. An
authored *tool result* is different -- the environment never said it -- which
is why grades stamp the full mix rather than a single flag.
"""

import json
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

from inspect_ai.log import EvalSample, read_eval_log
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelName,
    ModelOutput,
)
from inspect_ai.solver import TaskState
from inspect_ai.solver._task_state import sample_state
from inspect_ai.tool import (
    Tool,
    ToolCall,
    ToolDef,
    ToolError,
    ToolParams,
    ToolResult,
    tool,
)
from inspect_ai.util import StoreModel, sandbox, sandbox_default, store_as
from pydantic import BaseModel, Field, JsonValue

from ._sandbox import BENCHMARK_SERVICE, has_benchmark_box

Provenance = Literal["real", "enacted", "authored"]

AUTHORED_MODEL = "inspect_audit/authored"


class AttemptMessage(BaseModel):
    """One message of the reconstructed session, with how it got there."""

    provenance: Provenance
    message: ChatMessage


class BenchmarkState(StoreModel):
    """The benchmark session under reconstruction, one per audit sample."""

    seeded: str | None = None
    """Receipt for where the session came from: `new`, or a log slice."""

    messages: list[AttemptMessage] = Field(default_factory=list)
    output: ModelOutput | None = None
    attempt_store: dict[str, JsonValue] = Field(default_factory=dict)
    completed: bool = False
    box_version: int = 0
    """Bumped by `reset`: which state of the benchmark box grades ran against."""
    box_method: str = "initial"
    """How the box reached its current state: `initial`, `soft` (reverted in place),
    or `phoenix` (rebuilt from image). Stamped on grades so a receipt says whether
    the judged box was git-restored or genuinely rebuilt."""

    def chat_messages(self) -> list[ChatMessage]:
        """The session as plain messages, for a grader or a `TaskState`."""
        return [m.message for m in self.messages]

    def provenance_mix(self) -> dict[str, int]:
        """How synthetic the session is, as provenance -> message count."""
        mix: dict[str, int] = {}
        for m in self.messages:
            mix[m.provenance] = mix.get(m.provenance, 0) + 1
        return mix


def seed_new(state: BenchmarkState, input: JsonValue, prompt: str | None) -> None:
    """Seed the session at t=0: the item's own input, and any declared prompt.

    The input is the benchmark's own record, so it is `real`. The system
    message comes from the declared prompt *template*, not a recorded
    instantiation, so it is honestly `authored`.

    Args:
        state: The state to seed, replacing anything already there.
        input: The original sample's input: a string, or serialised messages.
        prompt: The declared prompt template, where one was recovered.
    """
    messages: list[AttemptMessage] = []
    if prompt:
        messages.append(
            AttemptMessage(provenance="authored", message=ChatMessageSystem(content=prompt))
        )
    if isinstance(input, str):
        messages.append(
            AttemptMessage(provenance="real", message=ChatMessageUser(content=input))
        )
    elif isinstance(input, list):
        for item in input:
            messages.append(AttemptMessage.model_validate({"provenance": "real", "message": item}))
    else:
        raise ValueError(f"cannot seed from input of type {type(input).__name__}")
    state.seeded = "new"
    state.messages = messages
    state.output = None
    state.attempt_store = {}
    state.completed = False


def seed_from_sample(state: BenchmarkState, sample: EvalSample, source: str) -> None:
    """Seed the session from a recorded attempt, verbatim.

    Args:
        state: The state to seed, replacing anything already there.
        sample: The recorded attempt, read from a sliced log.
        source: Receipt for where the attempt came from (log name and epoch).
    """
    state.seeded = source
    state.messages = [
        AttemptMessage(provenance="real", message=message) for message in sample.messages
    ]
    state.output = sample.output
    state.attempt_store = dict(sample.store or {})
    state.completed = True


def append_message(
    state: BenchmarkState,
    role: str,
    content: str,
    *,
    provenance: Provenance = "authored",
    tool_call_id: str | None = None,
    tool_calls: str | None = None,
) -> None:
    """Append one message to the session.

    Args:
        state: The state to append to.
        role: One of `system`, `user`, `assistant`, `tool`.
        content: The message content.
        provenance: How the message came to be (`authored` unless enacted).
        tool_call_id: For a `tool` message: the call it answers.
        tool_calls: For an `assistant` message: tool calls as a JSON list of
            `{id, function, arguments}` objects.
    """
    message: ChatMessage
    if role == "system":
        message = ChatMessageSystem(content=content)
    elif role == "user":
        message = ChatMessageUser(content=content)
    elif role == "assistant":
        calls = None
        if tool_calls:
            try:
                parsed = json.loads(tool_calls)
                calls = [
                    ToolCall(
                        id=str(c["id"]),
                        function=str(c["function"]),
                        arguments=dict(c.get("arguments") or {}),
                    )
                    for c in parsed
                ]
            except (ValueError, TypeError, KeyError) as ex:
                raise ValueError(
                    "tool_calls must be a JSON list of {id, function, arguments} "
                    f"objects: {ex}"
                ) from None
        message = ChatMessageAssistant(content=content, tool_calls=calls, model=AUTHORED_MODEL)
    elif role == "tool":
        if not tool_call_id:
            raise ValueError("a tool message needs the tool_call_id it answers")
        message = ChatMessageTool(content=content, tool_call_id=tool_call_id)
    else:
        raise ValueError(f"unknown role {role!r}: expected system, user, assistant or tool")
    state.messages = [*state.messages, AttemptMessage(provenance=provenance, message=message)]


def edit_message(state: BenchmarkState, index: int, content: str) -> None:
    """Replace one message's content; the edit marks it `authored`."""
    if not 0 <= index < len(state.messages):
        raise ValueError(f"index {index} out of range: session has {len(state.messages)} messages")
    edited = state.messages[index].message.model_copy(update={"content": content})
    state.messages = [
        *state.messages[:index],
        AttemptMessage(provenance="authored", message=edited),
        *state.messages[index + 1 :],
    ]


def truncate_messages(state: BenchmarkState, index: int) -> None:
    """Drop the session from `index` on."""
    if not 0 <= index <= len(state.messages):
        raise ValueError(f"index {index} out of range: session has {len(state.messages)} messages")
    state.messages = state.messages[:index]
    state.output = None
    state.completed = False


def complete_attempt(state: BenchmarkState, answer: str) -> None:
    """Fix the attempt's final answer and end it.

    The universal terminal: every attempt, whatever its tool vocabulary, ends
    by fixing a completion (possibly empty, when the answer is the state of
    the box). Appends the answer as an authored assistant turn so a
    transcript-reading scorer sees what a submitting agent's transcript shows.
    """
    if answer:
        append_message(state, "assistant", answer)
    state.output = ModelOutput.from_content(model=AUTHORED_MODEL, content=answer)
    state.completed = True


def receipt(state: BenchmarkState) -> str:
    """The session's current shape, as the tool's return value."""
    return json.dumps(
        {
            "seeded": state.seeded,
            "messages": len(state.messages),
            "provenance": state.provenance_mix(),
            "completed": state.completed,
            "box_version": state.box_version,
            "box_method": state.box_method,
        }
    )


def benchmark_task_state(
    current: TaskState,
    session: BenchmarkState,
    answer: str,
    *,
    model: str | None = None,
) -> TaskState:
    """The benchmark's own `TaskState`, for its grader to judge.

    Built from the benchmark's side of everything -- its input, choices and
    metadata carried on the audit sample, and the reconstructed session --
    the way `inspect score` rebuilds states from a log. The audit's own
    state supplies nothing but the target and the model name: a grader
    reading the question, the transcript or the store must see the
    benchmark's, never the audit's.

    Args:
        current: The audit's `TaskState` (for target and ids).
        session: The reconstructed benchmark session.
        answer: The submission under grade, as `output.completion`. Empty
            grades the benchmark environment exactly as it stands.
        model: Model identity to stamp on the graded state. Defaults to the
            auditor's. Set it to the evaluated model when regrading a recorded
            attempt, so a scorer reading `state.model` (a judge naming the model
            under test, a model-family gate) sees the model that produced the
            attempt rather than the auditor.
    """
    metadata = current.metadata or {}
    raw_input = metadata.get("benchmark_input") or ""
    input_messages: str | list[ChatMessage]
    if isinstance(raw_input, list):
        input_messages = [
            AttemptMessage.model_validate({"provenance": "real", "message": m}).message
            for m in raw_input
        ]
    else:
        input_messages = str(raw_input)
    item = metadata.get("audit_item") or {}
    return TaskState(
        model=ModelName(model) if model is not None else current.model,
        sample_id=item.get("sample_id", current.sample_id),
        epoch=current.epoch,
        input=input_messages,
        target=current.target,
        choices=metadata.get("benchmark_choices"),
        messages=session.chat_messages(),
        output=ModelOutput.from_content(model=AUTHORED_MODEL, content=answer),
        completed=True,
        metadata=dict(metadata.get("benchmark_metadata") or {}),
        store=dict(session.attempt_store),
    )


def record_enacted(
    state: BenchmarkState, name: str, arguments: dict[str, JsonValue], result: str
) -> None:
    """Record one executed benchmark tool call in the session.

    The call is a turn the auditor chose to make -- `authored`, since an agent
    could have made it -- and the result is what the box really returned, so it
    is `enacted`. Splitting them keeps a forged call sequence honestly separable
    from real environment output.

    Args:
        state: The session to append to.
        name: The benchmark tool that was called.
        arguments: The arguments it was called with.
        result: The tool's result, as text.
    """
    call_id = f"enacted-{len(state.messages)}"
    call = ChatMessageAssistant(
        content="",
        tool_calls=[ToolCall(id=call_id, function=name, arguments=dict(arguments))],
        model=AUTHORED_MODEL,
    )
    output = ChatMessageTool(content=result, tool_call_id=call_id, function=name)
    state.messages = [
        *state.messages,
        AttemptMessage(provenance="authored", message=call),
        AttemptMessage(provenance="enacted", message=output),
    ]


ATTEMPT_DIR = "attempt"


async def mirror_state(state: BenchmarkState, root: str) -> None:
    """Write the session into the auditor's box, so reading it is just files.

    Args:
        state: The session to mirror.
        root: The cell root (`/audit`).
    """
    await sandbox().write_file(
        f"{root}/{ATTEMPT_DIR}/messages.json",
        json.dumps([m.model_dump(exclude_none=True) for m in state.messages], indent=1, default=str),
    )
    await sandbox().write_file(f"{root}/{ATTEMPT_DIR}/state.json", receipt(state))


@tool
def attempt(root: str, prompt: str | None = None) -> Tool:
    """The reconstructed benchmark session, as a tool.

    Args:
        root: The cell root (`/audit`).
        prompt: The benchmark's declared prompt template, for `new`.
    """

    async def execute(command: str, args: str) -> str:
        """Build and edit the benchmark session that `grade` will judge.

        This is the evaluated agent's session as an object: seed it, write
        into it, end it, then grade it. It is mirrored under `attempt/` in
        the cell after every change, so read it with your shell. Every
        message records its provenance -- `real` (from a recorded attempt),
        `enacted` (actually executed against the benchmark box), `authored`
        (written by you) -- and grades are stamped with the mix, so a
        verdict's strength is legible from how synthetic its evidence was.

        `command` is one of `new`, `load`, `append`, `edit`, `truncate`,
        `complete`. `args` is a JSON object with that command's fields (pass
        `"{}"` for a command that needs none):

          new       {} -- start the session at t=0: the item's own input, plus
                    the benchmark's declared system prompt where recovered
          load      {"log": <filename as in logs/>, "epoch": <n, default 1>}
          append    {"role": "system|user|assistant|tool", "content": ...,
                    plus "tool_calls": JSON list of {id, function, arguments}
                    for an assistant message, or "tool_call_id" for a tool one}
          edit      {"index": <0-based>, "content": <replacement>}
          truncate  {"index": <cut from here>} -- counterfactuals: load a real
                    attempt, truncate at the turn under test, rebuild
          complete  {"answer": ...} -- fix the final answer and end the attempt;
                    "" when the submission is the state of the box

        Args:
            command: One of `new`, `load`, `append`, `edit`, `truncate`, `complete`.
            args: JSON object of the command's fields (`"{}"` when it needs none).
        """
        try:
            a = json.loads(args or "{}")
            if not isinstance(a, dict):
                raise ValueError("args must be a JSON object.")
        except ValueError:
            raise ToolError("args must be a JSON object.") from None

        state = store_as(BenchmarkState)
        try:
            if command == "new":
                current = sample_state()
                metadata = (current.metadata or {}) if current is not None else {}
                if "benchmark_input" not in metadata:
                    raise ValueError("this item carries no original input to seed from")
                seed_new(state, metadata["benchmark_input"], prompt)
            elif command == "load":
                log = a.get("log")
                if not log:
                    raise ValueError("load needs 'log', the filename as it appears in logs/")
                epoch = int(a.get("epoch") or 1)
                sample = await _read_sliced(root, str(log), epoch)
                seed_from_sample(state, sample, source=f"{log}#epoch={epoch}")
            elif command == "append":
                if not a.get("role") or a.get("content") is None:
                    raise ValueError("append needs 'role' and 'content'")
                append_message(
                    state,
                    str(a["role"]),
                    str(a["content"]),
                    tool_call_id=a.get("tool_call_id"),
                    tool_calls=(
                        json.dumps(a["tool_calls"]) if a.get("tool_calls") is not None else None
                    ),
                )
            elif command == "edit":
                if a.get("index") is None or a.get("content") is None:
                    raise ValueError("edit needs 'index' and 'content'")
                edit_message(state, int(a["index"]), str(a["content"]))
            elif command == "truncate":
                if a.get("index") is None:
                    raise ValueError("truncate needs 'index'")
                truncate_messages(state, int(a["index"]))
            elif command == "complete":
                complete_attempt(state, str(a.get("answer") or ""))
            else:
                raise ValueError(
                    f"unknown command {command!r}: expected new, load, append, "
                    "edit, truncate or complete"
                )
        except ValueError as ex:
            raise ToolError(str(ex)) from None
        await mirror_state(state, root)
        return receipt(state)

    return execute


# names a recovered tool that ends the attempt rather than acting on the box:
# in a react/basic_agent scaffold this is the submit tool, and enacting it means
# fixing the completion, not running it
_SUBMIT_NAMES = frozenset({"submit", "submit_answer", "complete", "finish"})


def benchmark_tools(defs: list[ToolDef], root: str) -> list[Tool]:
    """Mirror the evaluated agent's own tools for the auditor to enact.

    Each declared tool is remounted as `benchmark_<name>`, keeping the original
    schema, so the auditor holds exactly what the agent held. Calling one runs
    the real implementation against the benchmark box (the `sandbox_default`
    redirect that `grade` uses) and records the call and its result into the
    session, so enacting a tool builds the same transcript a real rollout would.
    A submit-shaped tool ends the attempt via `complete` instead of executing.

    Args:
        defs: The benchmark's declared tools, rebuilt from the registry.
        root: The cell root (`/audit`).
    """
    return [
        _submit_tool(d, root) if d.name in _SUBMIT_NAMES else _mirror_tool(d, root)
        for d in defs
    ]


def _strict_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """Make omitted arguments expressible as null for strict providers."""
    schema = dict(schema)
    schema.pop("default", None)
    if "properties" in schema:
        required = set(schema.get("required", []))
        properties = {}
        for name, value in schema["properties"].items():
            value = _strict_parameters(value)
            if name not in required:
                value = {"description": value.get("description", ""),
                         "anyOf": [value, {"type": "null"}]}
            properties[name] = value
        schema.update(properties=properties, required=list(properties), additionalProperties=False)
    if isinstance(schema.get("items"), dict):
        schema["items"] = _strict_parameters(schema["items"])
    for union in ("anyOf", "oneOf", "allOf"):
        if union in schema:
            schema[union] = [_strict_parameters(value) for value in schema[union]]
    return schema


def _restore_omissions(value: Any, schema: dict[str, Any]) -> Any:
    """Preserve the original callable's defaults when the mirror sends null."""
    if isinstance(value, dict) and "properties" in schema:
        required = set(schema.get("required", []))
        properties = schema["properties"]
        return {
            key: _restore_omissions(item, properties.get(key, {}))
            for key, item in value.items()
            if item is not None or key in required
        }
    if isinstance(value, list):
        return [_restore_omissions(item, schema.get("items", {})) for item in value]
    return value


def _mirror_tool(d: ToolDef, root: str) -> Tool:
    original = d.parameters.model_dump(exclude_none=True)

    async def execute(**kwargs: Any) -> ToolResult:
        # an explicit membership check: `sandbox(name)` resolves to the DEFAULT
        # environment on a one-environment sample, so the try/except this
        # replaced was dead code and the tool would have run in the auditor
        if not has_benchmark_box():
            raise ToolError(
                f"{d.name!r} runs in the benchmark environment, which this item "
                "does not have."
            )
        kwargs = _restore_omissions(kwargs, original)
        with sandbox_default(BENCHMARK_SERVICE):
            result = cast(ToolResult, await d.tool(**kwargs))
        session = store_as(BenchmarkState)
        record_enacted(session, d.name, dict(kwargs), _as_text(result))
        await mirror_state(session, root)
        return result

    return ToolDef(
        execute,
        name=f"benchmark_{d.name}",
        description=(
            f"The evaluated agent's `{d.name}` tool, run for real in the benchmark "
            "environment and recorded into the attempt. " + (d.description or "")
        ).strip(),
        parameters=ToolParams.model_validate(_strict_parameters(original)),
    ).as_tool()


def _submit_tool(d: ToolDef, root: str) -> Tool:
    # the agent's terminal action: enacting it fixes the answer and ends the
    # attempt, the way `complete` does -- running it against the box would do
    # nothing, since submit is scaffold loop control, not a real tool
    async def execute(answer: str) -> str:
        session = store_as(BenchmarkState)
        complete_attempt(session, answer)
        await mirror_state(session, root)
        return receipt(session)

    return ToolDef(
        execute,
        name=f"benchmark_{d.name}",
        description=(
            f"The evaluated agent's `{d.name}` tool: ends the attempt with the "
            "given answer, exactly as submitting did for the agent."
        ),
        parameters={"answer": "The attempt's final answer."},
    ).as_tool()


def _as_text(result: ToolResult) -> str:
    """A tool result as text, for the recorded tool message and the mirror."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = [c.text for c in result if c.type == "text"]
        return "\n".join(parts) if parts else json.dumps(result, default=str)
    return str(result)


async def _read_sliced(root: str, log: str, epoch: int) -> EvalSample:
    """Read one attempt from a sliced log in the cell.

    The cell is the source of truth, so the log is read out of the container
    rather than from host bookkeeping.
    """
    if "/" in log:
        raise ToolError("give the log's filename alone, as it appears in logs/")
    try:
        data = await sandbox().read_file(f"{root}/logs/{log}", text=False)
    except Exception as ex:
        raise ToolError(f"could not read logs/{log}: {ex}") from None
    assert isinstance(data, bytes)
    with tempfile.TemporaryDirectory(prefix="inspect_audit_attempt_") as staging:
        host = Path(staging) / log
        host.write_bytes(data)
        # read the slice whole: it holds only this item's attempts, so this is
        # one central-directory parse. the streaming reader would instead walk
        # the header's id list -- O(original dataset x epochs) doomed lookups
        # per load, ~24s measured on a 10k-sample four-epoch benchmark.
        sliced = read_eval_log(str(host), resolve_attachments=True)
        for sample in sliced.samples or []:
            if sample.epoch == epoch:
                return sample
    raise ToolError(f"no attempt with epoch {epoch} in logs/{log}")
