"""Proving the audit's own grade channel before it accuses the benchmark.

The pure logic here -- value normalisation, the classification of replay
results -- decides whether a cell's verdicts may stand. The end-to-end replay
against a real benchmark box is under Docker (`test_run.py`).
"""

from inspect_ai.log import EvalRevision, EvalSpec

from inspect_audit._concordance import (
    ScorerConcordance,
    _scorer_names,
    classify,
    normalize_value,
    resolution_report,
)


def test_normalize_collapses_equivalent_grade_shapes() -> None:
    assert normalize_value(1.0) == normalize_value(1) == "1"
    assert normalize_value("C") == normalize_value(" C ") == "C"
    assert normalize_value(True) == "True"
    assert normalize_value(0.5) == "0.5"
    # dicts compare by content regardless of key order
    assert normalize_value({"a": 1, "b": 2}) == normalize_value({"b": 2, "a": 1})


def con(scorer: str, checked: int, agreed: int, *, stable: int = 0, noisy: int = 0) -> ScorerConcordance:
    d = lambda n: [{"sample": str(i)} for i in range(n)]  # noqa: E731 - test brevity
    return ScorerConcordance(
        scorer=scorer, checked=checked, agreed=agreed,
        stable_disagreements=d(stable), noisy_disagreements=d(noisy),
    )


def test_perfect_replay_validates() -> None:
    verdict, reasons = classify([con("match", 10, 10)], has_box=False, attempted=10)
    assert verdict == "validated"


def test_stable_disagreement_without_a_box_blocks() -> None:
    # a deterministic, box-free scorer we cannot reproduce is OUR bug --
    # this is the cluster-1 catch, and it must block grade-dependent verdicts
    verdict, reasons = classify([con("match", 10, 4, stable=6)], has_box=False, attempted=10)
    assert verdict == "blocked"
    assert "reconstruction does not reproduce" in " ".join(reasons)


def test_disagreement_with_a_box_is_inconclusive_not_a_fault() -> None:
    # a box's end-state is not reproducible from a transcript, so a stable
    # disagreement there is inconclusive, not a reconstruction failure
    verdict, reasons = classify([con("test_pass", 10, 4, stable=6)], has_box=True, attempted=10)
    assert verdict == "inconclusive"
    assert "not reproducible from the transcript" in " ".join(reasons)


def test_judge_noise_never_blocks() -> None:
    # disagreements whose resamples flip are the scorer's own noise, reported as
    # a measured floor, never blocking
    verdict, reasons = classify([con("model_graded", 10, 7, noisy=3)], has_box=False, attempted=10)
    assert verdict == "validated"
    assert "scorer noise" in " ".join(reasons)


def test_one_flaky_sample_does_not_exempt_a_scorer_with_stable_faults() -> None:
    # the old bug: a single flipping sample marked the whole scorer nondeterministic
    # and exempt. Now a scorer with BOTH noise and stable faults still blocks.
    verdict, _ = classify(
        [con("match", 10, 6, stable=3, noisy=1)], has_box=False, attempted=10
    )
    assert verdict == "blocked"


def test_mixed_scorers_block_on_the_deterministic_one() -> None:
    verdict, _ = classify(
        [con("model_graded", 10, 7, noisy=3), con("match", 10, 3, stable=7)],
        has_box=False,
        attempted=10,
    )
    assert verdict == "blocked"


def test_resolution_report_flags_package_and_arg_drift() -> None:
    header = EvalSpec.model_construct(
        task="t",
        task_args={"difficulty": "hard"},
        # a version that is certainly not what is installed
        packages={"inspect_ai": "0.0.1-not-installed"},
        revision=EvalRevision(type="git", origin="o", commit="abc123", dirty=False),
    )
    report = resolution_report(header, task_args={"difficulty": "easy"})

    assert report.drifted
    assert report.package_drift["inspect_ai"]["logged"] == "0.0.1-not-installed"
    assert report.arg_drift["difficulty"] == {"logged": "hard", "resolved": "easy"}
    assert report.revisions[0]["commit"] == "abc123"


def test_resolution_report_clean_when_aligned() -> None:
    header = EvalSpec.model_construct(
        task="t", task_args={"difficulty": "hard"}, packages={}, revision=None
    )
    assert not resolution_report(header, task_args={"difficulty": "hard"}).drifted


def test_a_replay_that_checked_nothing_is_not_validated() -> None:
    """Zero comparisons must never read as a clean bill of health.

    Every failure mode of the replay machinery itself -- a scorer that raises,
    a key that no longer matches the log -- manifests as zero comparisons, not
    as a disagreement. The old default filed all of them as `validated`.
    """
    verdict, reasons = classify([], has_box=False, attempted=5)
    assert verdict == "unvalidated"
    assert "checked none" in " ".join(reasons)

    # machinery errors ride along verbatim
    verdict, reasons = classify(
        [], has_box=False, attempted=5, errors=["re-scoring failed: ValueError: boom"]
    )
    assert verdict == "unvalidated"
    assert any("ValueError: boom" in r for r in reasons)


def test_scorer_keys_match_the_log_for_duplicate_named_scorers(tmp_path) -> None:
    """The replay pairs grades by the keys the log actually wrote.

    Two same-named scorers get suffixed keys (`match`, `match1`) by inspect's
    recorder; keying any other way silently un-checks the second one.
    """
    from inspect_ai import Task, eval
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import includes, match

    scorers = [match(), match(location="begin"), includes()]
    log = eval(
        Task(
            name="dupes",
            dataset=MemoryDataset([Sample(id=1, input="q", target="ANSWER")]),
            scorer=scorers,
        ),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )[0]

    assert log.samples is not None
    assert _scorer_names(scorers) == list(log.samples[0].scores or {})


def _probe_reports(
    source_log: str | None, tmp_path, scorer_factory=None, mangle=None
) -> list[dict]:
    """Run the real concordance probe inside a real (pure, mockllm) audit eval."""
    from dataclasses import asdict

    from inspect_ai import eval
    from inspect_ai.scorer import match
    from inspect_ai.solver import Generate, Solver, TaskState, solver
    from test_helpers.logs import fixture_task

    from inspect_audit._audit import audit_task
    from inspect_audit._concordance import probe_concordance

    make_scorer = scorer_factory or match

    @solver
    def probe() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            report = await probe_concordance(state, [make_scorer()], has_box=False)
            state.store.set("report", asdict(report))
            return state

        return solve

    task = audit_task(fixture_task("graded_task"), source_log, solver=probe(), sandbox="local")
    for staged in task.dataset:
        staged.files = None  # nothing to stage into a local sandbox
        if mangle is not None:
            mangle(staged)
    log = eval(task, model="mockllm/model", log_dir=str(tmp_path / "audit"), display="none")[0]
    assert log.status == "success", log.error
    assert log.samples is not None
    return [s.store.get("report") for s in log.samples]


def test_replay_reproduces_recorded_grades_and_counts_them(graded_log: str, tmp_path) -> None:
    """A faithful channel validates BY CHECKING, not by default.

    The fixture's grades are mixed (C, I, C), so a regrade that agrees with
    anything cannot pass; and `checked == 1` is asserted because the old gate's
    failure mode was validating after comparing nothing at all.
    """
    for report in _probe_reports(graded_log, tmp_path):
        assert report["verdict"] == "validated", report["reasons"]
        (scorer,) = report["scorers"]
        assert scorer["scorer"] == "match"
        assert scorer["checked"] == 1
        assert scorer["agreed"] == 1


def test_a_tampered_recorded_grade_blocks(graded_log: str, tmp_path) -> None:
    """A recorded grade the replay cannot reproduce is a stable fault: blocked."""
    from inspect_ai.log import read_eval_log, write_eval_log

    log = read_eval_log(graded_log)
    assert log.samples is not None
    for sample in log.samples:
        for score in (sample.scores or {}).values():
            score.value = "I" if score.value == "C" else "C"
    tampered = str(tmp_path / "tampered.eval")
    write_eval_log(log, tampered)

    for report in _probe_reports(tampered, tmp_path):
        assert report["verdict"] == "blocked", report["reasons"]
        (scorer,) = report["scorers"]
        assert scorer["agreed"] == 0
        assert len(scorer["stable_disagreements"]) == 1


def test_a_replay_whose_machinery_fails_is_unvalidated(graded_log: str, tmp_path) -> None:
    """The exact bug this gate had: total failure must not read as validated.

    A REAL raising scorer, not a mock of the machinery: every failure mode of
    the channel (a scorer that raises, an absent provider) lands in the same
    except, and the error text must reach the report -- swallowed, a broken
    channel reads as a clean one.
    """
    from inspect_ai.scorer import Score, Target, accuracy, scorer
    from inspect_ai.solver import TaskState

    @scorer(metrics=[accuracy()], name="match")
    def exploding():
        async def score(state: TaskState, target: Target) -> Score:
            raise ValueError("scorer exploded")

        return score

    for report in _probe_reports(graded_log, tmp_path, scorer_factory=exploding):
        assert report["verdict"] == "unvalidated", report["verdict"]
        assert not any("reproduced every recorded grade" in r for r in report["reasons"])
        # the machinery's own error reaches the report verbatim
        assert any(
            "re-scoring failed" in r and "ValueError" in r for r in report["reasons"]
        ), report["reasons"]


def test_failed_resamples_count_as_stable_never_as_noise(monkeypatch) -> None:
    """Resamples that all ERROR prove nothing about noise.

    Scoring the unknown as benign would let a flaky channel excuse a real
    reconstruction fault; conservatively it stays stable (blockable).
    """
    import asyncio
    from types import SimpleNamespace

    from inspect_ai.scorer import Score, match

    import inspect_audit._concordance as concordance_module
    from inspect_audit._concordance import replay_regrade

    calls = {"n": 0}

    async def first_disagrees_then_fails(samples, scorers, header_log):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"match": Score(value="C")} for _ in samples], None
        return [None] * len(samples), "re-scoring failed: RuntimeError: box gone"

    monkeypatch.setattr(concordance_module, "_score_batch", first_disagrees_then_fails)
    recorded = SimpleNamespace(id=1, epoch=1, scores={"match": Score(value="I")})

    scored, errors = asyncio.run(
        replay_regrade([recorded], [match()], header_log=None, limit=5)
    )

    (result,) = scored
    assert len(result.stable_disagreements) == 1
    assert result.noisy_disagreements == []


def test_probe_is_unvalidated_with_no_attempts(tmp_path) -> None:
    """An audit with no logs has nothing to replay: unproven, never validated.

    This early return is guarded ONLY by the dataclass default, so this pins it.
    """
    for report in _probe_reports(None, tmp_path):
        assert report["verdict"] == "unvalidated", report["reasons"]
        assert any("no recorded attempts" in r for r in report["reasons"])


def test_probe_is_unvalidated_when_the_log_header_is_unreadable(
    graded_log: str, tmp_path
) -> None:
    """A vanished/corrupt source log cannot validate anything."""

    def point_refs_nowhere(staged) -> None:
        for ref in staged.metadata["audit_item"]["attempts"]:
            ref["log_file"] = str(tmp_path / "gone.eval")

    for report in _probe_reports(graded_log, tmp_path, mangle=point_refs_nowhere):
        assert report["verdict"] == "unvalidated", report["reasons"]
        assert any("could not read a log header" in r for r in report["reasons"])
