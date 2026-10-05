"""Opt-in paid provider check: INSPECT_AUDIT_LIVE_TESTS=1 pytest tests/test_provider_smoke.py.

Set OPENROUTER_API_KEY in the environment. No benchmark data is sent.
"""
import asyncio
import json
import os

import pytest


@pytest.mark.skipif(os.getenv("INSPECT_AUDIT_LIVE_TESTS") != "1", reason="opt-in provider call")
@pytest.mark.parametrize("all_items", [False, True])
def test_verdict_schema_at_provider(all_items: bool) -> None:
    from inspect_ai.model import GenerateConfig, get_model
    from inspect_ai.scorer import match
    from inspect_ai.tool import ToolDef, ToolFunction, bash, python, text_editor, think

    from inspect_audit._agent import audit_items, auditor_tools, submit_audit
    from inspect_audit._contract import SolverContract

    items = audit_items()
    if not all_items:
        items = items[:1]
    item = items[0]
    details = {key: "Synthetic plumbing check" for key in item.details}
    prompt = (
        "This is a synthetic tool-schema test. Call record_verdict once with "
        f"item={item.name!r}, grade={item.grades[0]!r}, "
        'evidence=[{"observed":"synthetic observation","source":"synthetic.txt:1"}], '
        'approaches="synthetic", tried="schema check", remarks="synthetic". '
        f"The details argument must be the JSON string {json.dumps(details)!r}."
    )

    async def run():
        model = get_model(
            "openrouter/openai/gpt-5-mini",
            config=GenerateConfig(max_tokens=2048, reasoning_effort="low", max_retries=0),
        )
        output = await model.generate(
            prompt, tools=auditor_tools(items, benchmark_scorers=match(), media=True,
                contract=SolverContract(tools=[ToolDef(t()) for t in (bash, python, text_editor, think)]),
                benchmark=True) + [submit_audit(items)],
            tool_choice=ToolFunction("record_verdict")
        )
        calls = output.message.tool_calls or []
        assert len(calls) == 1
        assert calls[0].function == "record_verdict"
        assert isinstance(calls[0].arguments["details"], str)
        assert json.loads(calls[0].arguments["details"]) == details

    asyncio.run(run())
