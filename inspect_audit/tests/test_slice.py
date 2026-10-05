"""Slicing real logs down to one item.

The premise of the case filesystem is that what lands in the sandbox is a genuine
Inspect log, not a summary of one. So these tests read the slice back through
Inspect's own API — if it were subtly not a log, `read_eval_log` is what would notice.
"""

from pathlib import Path

import pytest
from inspect_ai.log import (
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_sample_summaries,
    write_eval_log,
)
from test_helpers.logs import run_fixture_eval

from inspect_audit._item import AttemptRef, sample_logs


def refs(log: str, sample_id: str, *epochs: int) -> list[AttemptRef]:
    return [
        AttemptRef(model="mockllm/model", epoch=epoch, log_file=log, sample_id=sample_id)
        for epoch in epochs
    ]


def test_slice_is_a_real_log_holding_only_the_audited_item(
    fixture_log: str, tmp_path: Path
) -> None:
    files, _ = sample_logs(refs(fixture_log, "2", 1), stage=tmp_path)
    sliced = next(iter(files.values()))

    log = read_eval_log(sliced)
    assert log.status == "success"
    assert [(s.id, s.epoch) for s in (log.samples or [])] == [(2, 1)]
    # The cheap reading paths work too, which is how an auditor will actually use it.
    assert [(s.id, s.epoch) for s in read_eval_log_sample_summaries(sliced)] == [(2, 1)]
    assert read_eval_log_sample(sliced, id=2, epoch=1).input == "q2"


def test_header_survives_except_the_dataset_ids_which_narrow(
    fixture_log: str, tmp_path: Path
) -> None:
    """The header records how the attempt was elicited and graded -- keep it.

    Recovering those facts ourselves would hand the auditor a schema of ours to
    trust, so everything is equality against the source header. The one
    deliberate exception is the dataset id list: inspect's streaming reader
    walks it, so a verbatim list of the WHOLE original dataset makes
    `read_eval_log_samples` raise on every log the audit stages.
    """
    files, _ = sample_logs(refs(fixture_log, "1", 1), stage=tmp_path)
    sliced_file = next(iter(files.values()))
    before = read_eval_log(fixture_log, header_only=True)
    after = read_eval_log(sliced_file, header_only=True)

    assert after.eval.dataset.sample_ids == [1]
    assert after.eval.dataset.samples == 1
    without_dataset = {"dataset": None}
    assert after.eval.model_copy(update=without_dataset) == before.eval.model_copy(
        update=without_dataset
    )  # task, model, scorers, config, packages, args
    assert after.plan == before.plan  # solver chain and generate config

    # and the point of the narrowing: inspect's own default reader works
    from inspect_ai.log import read_eval_log_samples

    assert [(s.id, s.epoch) for s in read_eval_log_samples(sliced_file)] == [(1, 1)]


def test_every_epoch_of_an_item_stays_together(
    fixture_log_epochs: str, tmp_path: Path
) -> None:
    files, _ = sample_logs(refs(fixture_log_epochs, "1", 1, 2, 3), stage=tmp_path)
    log = read_eval_log(next(iter(files.values())))
    assert sorted(s.epoch for s in (log.samples or [])) == [1, 2, 3]


def test_one_file_per_source_log_keeping_its_name(fixture_log: str, tmp_path: Path) -> None:
    """A case reads like the logs it came from, so names are not rewritten."""
    other = run_fixture_eval(str(tmp_path / "other"), name="other_task")
    files, _ = sample_logs(
        refs(fixture_log, "1", 1) + refs(other, "1", 1), stage=tmp_path / "case"
    )

    assert {Path(p).name for p in files} == {
        Path(log.replace("file://", "")).name for log in (fixture_log, other)
    }


def test_an_unreadable_log_costs_one_model_not_the_case(
    fixture_log: str, tmp_path: Path
) -> None:
    files, _ = sample_logs(
        refs(fixture_log, "1", 1) + refs(str(tmp_path / "missing.eval"), "1", 1),
        stage=tmp_path / "case",
    )
    assert len(files) == 1


@pytest.mark.parametrize("shared_eval_id", [True, False])
def test_colliding_names_preserve_every_source_and_its_attempt(
    fixture_log: str, tmp_path: Path, shared_eval_id: bool
) -> None:
    """Different evaluations and copies/rescores sharing an ID must survive.

    Also reserve a real suffixed filename so collision handling cannot overwrite
    a third source. Read every slice through Inspect to verify its identity and
    content, not just the number of filenames returned.
    """
    sources = []
    for directory, name in (("a", "same.eval"), ("b", "same.eval"), ("c", "same-2.eval")):
        source = tmp_path / directory / name
        source.parent.mkdir()
        log = read_eval_log(fixture_log)
        log.eval.eval_id = "shared-evaluation" if shared_eval_id else f"evaluation-{directory}"
        log.eval.model = f"mockllm/{directory}"
        assert log.samples
        log.samples[0].input = f"question from {directory}"
        write_eval_log(log, str(source))
        sources.extend(refs(str(source), "1", 1))

    files, tools = sample_logs(sources, stage=tmp_path / "case")

    assert len(files) == 3
    assert set(tools) == {Path(path).name for path in files}
    observed = {}
    for path in files.values():
        log = read_eval_log(path)
        directory = log.eval.model.rsplit("/", 1)[-1]
        assert log.eval.eval_id == (
            "shared-evaluation" if shared_eval_id else f"evaluation-{directory}"
        )
        assert log.samples and len(log.samples) == 1
        observed[log.eval.model] = log.samples[0].input
    assert observed == {
        f"mockllm/{directory}": f"question from {directory}"
        for directory in ("a", "b", "c")
    }

    # Assignment depends on source identity, not the incoming attempt order.
    reordered, _ = sample_logs(list(reversed(sources)), stage=tmp_path / "reordered")
    assert {
        Path(path).name: read_eval_log(path, header_only=True).eval.model
        for path in reordered.values()
    } == {
        Path(path).name: read_eval_log(path, header_only=True).eval.model
        for path in files.values()
    }


def test_the_graders_own_model_calls_are_not_the_agents_tools() -> None:
    """A model-graded scorer's extractor/judge calls sit under the scorers span."""
    from inspect_ai.event import ModelEvent, SpanBeginEvent, SpanEndEvent
    from inspect_ai.model import ChatMessageUser, ModelOutput
    from inspect_ai.tool import ToolInfo, ToolParams

    from inspect_audit._item import _solver_events

    def model_event(span: str, tools: list[str]) -> ModelEvent:
        return ModelEvent(
            model="m",
            input=[ChatMessageUser(content="x")],
            tools=[ToolInfo(name=t, description="", parameters=ToolParams()) for t in tools],
            tool_choice="auto",
            config={},
            output=ModelOutput.from_content("m", "y"),
            span_id=span,
        )

    events = [
        SpanBeginEvent(id="solvers", name="solvers", type="solvers"),
        SpanBeginEvent(id="gen", parent_id="solvers", name="generate", type="solver"),
        model_event("gen", ["bash"]),
        SpanEndEvent(id="gen"),
        SpanEndEvent(id="solvers"),
        SpanBeginEvent(id="scorers", name="scorers", type="scorers"),
        SpanBeginEvent(id="judge", parent_id="scorers", name="judge", type="scorer"),
        model_event("judge", ["submit"]),
        SpanEndEvent(id="judge"),
        SpanEndEvent(id="scorers"),
    ]
    seen = {t.name for e in _solver_events(events) if isinstance(e, ModelEvent) for t in e.tools}
    assert seen == {"bash"}
