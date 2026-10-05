"""Assembling the audit task, and resolving what is being audited.

No network and no Docker: `audit_task` accepts an already-constructed `Task`, so
these exercise the assembly without resolving a published benchmark.
"""

from pathlib import Path

import pytest
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match
from test_helpers.logs import fixture_task, run_fixture_eval

from inspect_audit import audit_task, resolve_task
from inspect_audit._audit import attempts
from inspect_audit._item import AUDIT_ROOT
from inspect_audit._sandbox import audit_sandbox


def make_task(n: int = 3, with_ids: bool = True) -> Task:
    samples = [
        Sample(id=(100 + i) if with_ids else None, input=f"question {i}", target=str(i))
        for i in range(n)
    ]
    return Task(name="fixture_task", dataset=MemoryDataset(samples), scorer=match())


def test_resolve_task_passes_a_task_through() -> None:
    task = make_task()
    assert resolve_task(task) is task


def test_resolve_task_names_the_spec_it_could_not_resolve() -> None:
    with pytest.raises(ValueError, match="no_such_package/no_such_task"):
        resolve_task("no_such_package/no_such_task")


def test_audit_task_makes_one_item_per_sample() -> None:
    audit = audit_task(make_task(3))

    assert audit.name == "audit/fixture_task"
    assert len(audit.dataset) == 3
    assert audit.metadata is not None and audit.metadata["audited_task"] == "fixture_task"


def test_limit_and_sample_selection() -> None:
    assert len(audit_task(make_task(5), limit=2).dataset) == 2

    selected = audit_task(make_task(5), samples=[101, 103]).dataset
    assert [s.id for s in selected] == ["101", "103"]


def test_samples_without_ids_are_addressed_by_position() -> None:
    # Inspect assigns 1-based ids when a dataset does not set them, so an audit
    # must address them the same way or the join to logs silently mismatches.
    audit = audit_task(make_task(3, with_ids=False))
    assert [s.id for s in audit.dataset] == ["1", "2", "3"]


def test_each_item_gets_a_sandbox_and_a_filesystem() -> None:
    item = audit_task(make_task(1)).dataset[0]

    assert item.sandbox is not None
    assert item.files is not None
    assert f"{AUDIT_ROOT}/sample.json" in item.files


def test_the_audited_tasks_environment_runs_alongside_the_auditors(tmp_path: Path) -> None:
    """Their services keep their definitions; ours is the default environment.

    An audit of what an environment affords is only valid against the environment as it
    was, so the merge never edits their service - not its name, not its network, not the
    hosts it null-routes.
    """
    their_compose = tmp_path / "their-compose.yaml"
    their_compose.write_text(
        'services:\n'
        '  default:\n'
        '    build: .\n'
        '    extra_hosts:\n'
        '      - "codeocean.com:127.0.0.1"\n'
    )
    task = make_task(1)
    task.sandbox = ("docker", str(their_compose))  # type: ignore[assignment]

    sandbox = audit_task(task).dataset[0].sandbox
    assert sandbox is not None and sandbox.type == "docker"
    merged = Path(str(sandbox.config)).read_text()

    import yaml

    services = yaml.safe_load(merged)["services"]
    # ours is `default`, because Inspect treats that name as an alias for the default
    # environment rather than as a service lookup, so a benchmark service called
    # `default` would be unaddressable.
    assert set(services) == {"default", "benchmark"}
    # theirs keeps its definition, including the hosts it deliberately null-routes
    assert services["benchmark"]["extra_hosts"] == ["codeocean.com:127.0.0.1"]
    # and its relative build context is re-anchored to its own directory
    assert services["benchmark"]["build"] == str(tmp_path)

    ours = audit_task(make_task(1)).dataset[0].sandbox
    assert ours is not None
    assert ours.type == "docker"
    assert ours.config is not None


def test_every_item_records_which_item_it_audits() -> None:
    for item in audit_task(make_task(2)).dataset:
        audited = (item.metadata or {})["audit_item"]
        assert audited["task"] == "fixture_task"
        assert str(audited["sample_id"]) == str(item.id)
        assert audited["attempts"] == []  # no logs were given


def test_the_generated_sandbox_pins_the_tasks_own_packages() -> None:
    from inspect_audit._sandbox import task_requirements

    reqs = task_requirements(make_task(1))
    # inspect-ai always, pinned to the resolving environment's version.
    assert any(r.startswith("inspect-ai==") for r in reqs)
    assert all("==" in r for r in reqs)


def test_attempts_join_only_the_audited_tasks_logs(tmp_path: Path) -> None:
    """Sample ids are unique only within a task, so the join must filter on task.

    Two tasks in one logs directory both number their samples 1..N. Joining on id
    alone would attach the other task's attempts to these items -- foreign
    transcripts graded as if they were attempts at this question.
    """
    logs = str(tmp_path / "logs")
    run_fixture_eval(logs, name="audited_task")
    run_fixture_eval(logs, name="other_task")

    mine = attempts(logs, task="audited_task")
    assert set(mine["task_name"].astype(str).unique()) == {"audited_task"}
    # both tasks have samples with id "1"; only the audited task's is joined
    assert len(mine[mine["id"].astype(str) == "1"]) == 1

    with pytest.raises(ValueError, match="record task 'absent_task'"):
        attempts(logs, task="absent_task")


def test_audit_task_does_not_attach_a_foreign_tasks_attempts(tmp_path: Path) -> None:
    """End to end: audit one task from a dir that also holds another task's logs."""
    logs = str(tmp_path / "logs")
    run_fixture_eval(logs, name="fixture_task")
    run_fixture_eval(logs, name="decoy_task")

    audit = audit_task(fixture_task(), logs, samples=["1"])
    item = audit.dataset[0].metadata["audit_item"]  # type: ignore[index]
    assert item["attempts"], "the audited task's own attempt should be joined"
    for attempt in item["attempts"]:
        assert attempt["sample_id"] in ("1", 1)
    # exactly the one fixture_task attempt at sample 1, not the decoy's too
    assert len(item["attempts"]) == 1


def test_the_generated_sandbox_has_network_access() -> None:
    """An auditor without egress cannot read a source, and will invent one instead.

    Inspect's own generated compose for a Dockerfile sandbox sets
    `network_mode: none`, which is why we write our own. We give the auditor egress
    via the shared `bridge` (not a named/project network), so it allocates no
    per-sample network and can't exhaust Docker's address pool at concurrency.
    """
    sandbox = audit_sandbox(make_task(1))
    assert isinstance(sandbox, tuple)
    compose = Path(sandbox[1])
    assert compose.name == "compose.yaml"
    assert (compose.parent / "Dockerfile").is_file()
    text = compose.read_text()
    assert "network_mode: bridge" in text  # egress, zero per-sample networks
    assert "network_mode: none" not in text


def test_attempts_can_come_from_a_sibling_variant_of_the_audited_task(
    fixture_log: str,
) -> None:
    """The same items often ship under several variants; the field's answers still count.

    Benchmarks routinely run the same questions as `<name>` and `<name>_tools`
    variants with identical sample ids. Auditing the tools task -- the one with a
    real environment -- while joining the no-tools attempts is the only way to get
    both a benchmark box and an evidence base. Each sliced log keeps its own header, so the
    auditor can see which variant produced each attempt.
    """
    from inspect_audit import attempts

    recorded = attempts(fixture_log)
    name = str(recorded["task_name"].iloc[0]).split("/")[-1]

    # the guard still fires for a genuinely unrelated task
    with pytest.raises(ValueError, match="None of these logs record task"):
        attempts(fixture_log, task="some_other_benchmark")

    # ...but an explicit sibling name is honoured
    assert not attempts(fixture_log, task=name).empty



def test_resolve_task_accepts_either_registry_name_form(monkeypatch) -> None:  # noqa: ANN001
    """`pkg/name` and bare `name` both resolve, whichever way the package was installed."""
    from inspect_audit import _resolve

    registered = {"pkg/thing": object()}

    def load_tasks(specs, args):  # noqa: ANN001, ANN202
        return [registered[specs[0]]] if specs[0] in registered else []

    monkeypatch.setattr(_resolve, "load_tasks", load_tasks)
    monkeypatch.setattr(_resolve, "_registry_packages", lambda: ["pkg"])
    assert _resolve.resolve_task("pkg/thing") is registered["pkg/thing"]
    assert _resolve.resolve_task("thing") is registered["pkg/thing"]
    registered.clear()
    registered["thing"] = object()
    assert _resolve.resolve_task("pkg/thing") is registered["thing"]
    with pytest.raises(ValueError, match="Tried"):
        _resolve.resolve_task("pkg/missing")
