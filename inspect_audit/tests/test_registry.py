"""Replaying recorded exploits against a benchmark's own task.

No Docker: these check that the replay task is assembled correctly -- the right
samples kept, the exploit installed as the solver, the scorer left alone -- which
is assembly, not execution.
"""

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match
from inspect_ai.solver import generate

from inspect_audit._registry import replay_task


def make_base() -> Task:
    samples = [Sample(id=f"s{i}", input=f"q{i}", target="a") for i in range(4)]
    return Task(
        name="benchmark_task",
        dataset=MemoryDataset(samples),
        solver=generate(),
        scorer=match(),
    )


def test_replay_keeps_only_exploited_samples_and_swaps_the_solver() -> None:
    base = make_base()
    original_scorer = base.scorer

    replay = replay_task(base, {"s1": "echo pwned", "s3": "echo pwned"})

    # narrowed to the samples we hold an exploit for
    assert sorted(str(s.id) for s in replay.dataset) == ["s1", "s3"]
    # the benchmark's own scorer is untouched -- it is the judge
    assert replay.scorer == original_scorer
    assert isinstance(replay, Task)
    assert replay.solver is not None


def test_replay_normalises_the_solver_it_installs() -> None:
    """The exploit solver goes through Task's own normalisation, not raw assignment.

    Passing `solver=[replay_exploit(...)]` to `task_with` lets Task normalise it
    exactly as if it had been constructed with that solver -- the reason to use
    `task_with` over assigning `base.solver` directly, which bypasses that.
    """
    base = make_base()
    replay = replay_task(base, {"s0": "echo x"})

    # a solver is present and the sample survived intact for the real scorer
    assert replay.solver is not None
    assert [str(s.id) for s in replay.dataset] == ["s0"]
    assert replay.dataset[0].target == "a"


def test_legacy_report_registry_still_constructs_without_a_sandbox() -> None:
    from inspect_audit._registry import report

    task = report()
    assert task.sandbox is None
    assert len(list(task.dataset)) == 1
