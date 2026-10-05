"""One end-to-end run under `mockllm`.

The unit tests only assemble tasks, which cannot catch anything that fails while an
eval actually executes -- an unregistered solver, a sandbox that will not build, a
file that will not copy, a log that arrives but will not open. This runs the real
thing, with no model spend.

Requires Docker; deselect with `-m "not docker"`.
"""

import json
from pathlib import Path
from textwrap import dedent

import pytest
from inspect_ai import eval
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox
from test_helpers.logs import fixture_task, run_fixture_eval

from inspect_audit import audit_task
from inspect_audit._item import AUDIT_ROOT

pytestmark = pytest.mark.docker

# run inside the sandbox with the audited task's own inspect_ai, to prove the item's
# logs are genuine logs rather than files that merely arrived
READ_LOGS = dedent(f"""
    import glob, json
    from inspect_ai.log import read_eval_log

    out = []
    for file in sorted(glob.glob("{AUDIT_ROOT}/logs/*.eval")):
        log = read_eval_log(file)
        out.append(
            dict(
                file=file,
                task=log.eval.task,
                model=log.eval.model,
                samples=len(log.samples or []),
                scorers=[s.name for s in (log.eval.scorers or [])],
            )
        )
    print(json.dumps(out))
""")


@solver
def probe_sandbox() -> Solver:
    """Read the item filesystem back out of the sandbox, in place of the agent."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        listing = await sandbox().exec(["find", AUDIT_ROOT, "-type", "f", "-print"], timeout=60)
        if not listing.success:
            raise RuntimeError(f"could not list {AUDIT_ROOT}: {listing.stderr}")
        state.store.set("audit_files", sorted(listing.stdout.splitlines()))

        probe = await sandbox().exec(["python", "-c", READ_LOGS], timeout=300)
        if not probe.success:
            raise RuntimeError(f"could not read the item logs in the sandbox: {probe.stderr}")
        state.store.set("logs_readable", json.loads(probe.stdout))
        return state

    return solve


def test_a_container_is_spun_out_and_its_logs_open_inside_it(tmp_path: Path) -> None:
    """The item's logs must be usable by the audited task's own `inspect_ai`.

    Uses `probe_sandbox` rather than the auditing agent: this test is about the
    container, and `mockllm` cannot drive a react loop to a submission.
    """
    source = run_fixture_eval(str(tmp_path / "source"))

    log = eval(
        audit_task(fixture_task(), source, solver=probe_sandbox()),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None and len(log.samples) == 3

    for sample in log.samples:
        readable = sample.store.get("logs_readable")
        assert readable, f"no logs opened in the container for item {sample.id}"
        for entry in readable:
            # header survived into the container, and the slice holds this item only
            assert entry["task"] == "fixture_task"
            assert entry["model"] == "mockllm/model"
            assert entry["scorers"] == ["match"]
            assert entry["samples"] == 1


def test_the_benchmark_box_receives_the_samples_own_state(tmp_path: Path) -> None:
    """A benchmark's environment is image plus per-sample state, not image alone."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import match
    from inspect_ai.solver import generate

    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.12-slim\n"
        "    command: 'sleep infinity'\n    working_dir: /work\n"
    )
    payload = tmp_path / "payload.txt"
    payload.write_text("the sample's own state")

    bench = Task(
        name="probe/bench",
        dataset=[
            Sample(
                input="q",
                target="a",
                id="one",
                files={"payload.txt": str(payload)},
                setup="cp /work/payload.txt /work/setup-ran.txt",
                sandbox=("docker", str(compose)),
            )
        ],
        solver=generate(),
        scorer=match(),
    )

    @solver
    def content_probe() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            r = await sandbox("benchmark").exec(
                ["cat", "/work/payload.txt", "/work/setup-ran.txt"], timeout=60
            )
            state.store.set("content", r.stdout if r.success else f"FAIL {r.stderr}")
            return state

        return solve

    log = eval(
        audit_task(bench, samples=["one"], solver=content_probe()),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None
    content = log.samples[0].store.get("content")
    assert content == "the sample's own state" * 2, content


def test_a_mirrored_tool_enacts_in_the_box_and_records_the_call(tmp_path: Path) -> None:
    """A benchmark_* tool runs for real in the benchmark box and lands in the attempt."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import match
    from inspect_ai.solver import generate
    from inspect_ai.tool import ToolDef, bash

    from inspect_audit._state import BenchmarkState, benchmark_tools

    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.12-slim\n"
        "    command: 'sleep infinity'\n    working_dir: /work\n"
    )
    bench = Task(
        name="probe/bench",
        dataset=[Sample(input="q", target="a", id="one", sandbox=("docker", str(compose)))],
        solver=generate(),
        scorer=match(),
    )

    @solver
    def enact_probe() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            # the mirrored bash tool, exactly as the auditor would hold it
            (mirrored,) = benchmark_tools([ToolDef(bash())], AUDIT_ROOT)
            await mirrored(command="echo enacted > /work/proof.txt")

            proof = await sandbox("benchmark").exec(["cat", "/work/proof.txt"], timeout=60)
            state.store.set("in_box", proof.stdout.strip() if proof.success else f"FAIL {proof.stderr}")

            session = state.store_as(BenchmarkState)
            state.store.set("provenance", session.provenance_mix())
            state.store.set("recorded_call", session.messages[0].message.tool_calls[0].function)
            return state

        return solve

    log = eval(
        audit_task(bench, samples=["one"], solver=enact_probe()),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None
    store = log.samples[0].store
    # the tool really ran in the benchmark box
    assert store.get("in_box") == "enacted"
    # and it was recorded as an authored call with an enacted result
    assert store.get("recorded_call") == "bash"
    assert store.get("provenance") == {"authored": 1, "enacted": 1}


# The concordance replay is covered by pure tests in test_concordance.py
# (faithful/tampered/broken-machinery); it needs no container, so it does not
# spend a docker slot here.


def test_phoenix_reset_revives_a_bricked_benchmark_box(tmp_path: Path) -> None:
    """A hard reset, driven through the `reset` tool, revives a truly-dead box.

    The brick (`kill 1`) genuinely exits the container -- proven by asserting the
    box is unreachable *before* the reset -- so a soft in-place reset could not
    recover it. The sample seeds state through both channels a real benchmark uses:
    a setup script (state.txt) and `Sample.files` (given.txt). A rebuild wipes the
    container filesystem, so recovering *both* proves the load-bearing pieces end to
    end: the same `sandbox("benchmark")` handle late-binds to the fresh container,
    setup re-runs, and file-delivered state is re-laid (not just setup). The whole
    chain runs through the real tool -- `_item` stashing `benchmark_files`, the tool
    threading it, `phoenix_benchmark` replaying it.
    """
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import match
    from inspect_ai.solver import generate

    from inspect_audit._agent import reset_benchmark

    given = tmp_path / "given.txt"
    given.write_text("delivered")
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "services:\n  default:\n    image: python:3.12-slim\n"
        "    init: true\n    command: 'sleep infinity'\n    working_dir: /work\n"
    )
    bench = Task(
        name="probe/bench",
        dataset=[
            Sample(
                input="q",
                target="a",
                id="one",
                files={"/work/given.txt": str(given)},  # the files channel
                setup="mkdir -p /work && echo pristine > /work/state.txt",  # setup channel
                sandbox=("docker", str(compose)),
            )
        ],
        solver=generate(),
        scorer=match(),
    )

    async def _cat(path: str) -> str:
        r = await sandbox("benchmark").exec(["cat", path], timeout=60)
        return r.stdout.strip() if r.success else f"FAIL {r.stderr}"

    @solver
    def brick_and_phoenix() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            # both seeding channels present at the start
            state.store.set("setup_before", await _cat("/work/state.txt"))
            state.store.set("files_before", await _cat("/work/given.txt"))

            # BRICK: kill PID 1 -- the container exits.
            try:
                await sandbox("benchmark").exec(["sh", "-c", "kill 1"], timeout=30)
            except Exception:
                pass
            # prove the box is ACTUALLY down before the reset, so the test cannot
            # pass by a rebuild that was never needed
            probe = await sandbox("benchmark").exec(["true"], timeout=20)
            state.store.set("box_down_after_brick", not probe.success)

            # drive the real reset tool (hard) -- exercises the whole metadata->files
            # ->phoenix chain, not just phoenix_benchmark in isolation
            state.store.set("reset", await reset_benchmark()(hard=True))

            # the same handle reaches the rebuilt container; BOTH channels restored
            state.store.set("setup_after", await _cat("/work/state.txt"))
            state.store.set("files_after", await _cat("/work/given.txt"))

            # the auditor box is untouched: a file copied into it at init still there
            audit = await sandbox().exec(["cat", "/audit/sample.json"], timeout=60)
            state.store.set("auditor_file_survived", audit.success and bool(audit.stdout.strip()))
            return state

        return solve

    log = eval(
        audit_task(bench, samples=["one"], solver=brick_and_phoenix()),
        model="mockllm/model",
        log_dir=str(tmp_path / "audit"),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert log.samples is not None
    store = log.samples[0].store
    assert store.get("setup_before") == "pristine"
    assert store.get("files_before") == "delivered"
    assert store.get("box_down_after_brick") is True  # the brick truly killed it
    assert "rebuilt from image" in (store.get("reset") or "")
    assert store.get("setup_after") == "pristine"  # setup channel re-run
    assert store.get("files_after") == "delivered"  # files channel re-laid (fix #1)
    assert store.get("auditor_file_survived") is True  # auditor never recreated
