"""The benchmark environment: reproduction, restoration, and unit conversion.

No Docker: these exercise the compose synthesis and the restore ordering with a
fake sandbox, so the contract is checked without spinning up containers.
"""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match
from inspect_ai.util import SandboxEnvironmentSpec

import inspect_audit._sandbox as sandbox_module
from inspect_audit._sandbox import (
    _seconds,
    audit_compose,
    has_benchmark,
    phoenix_benchmark,
    restore_benchmark,
)


def make_task() -> Task:
    dataset = MemoryDataset([Sample(input="question", target="answer")])
    return Task(name="fixture_task", dataset=dataset, scorer=match())


def test_restore_reverts_git_state_before_rerunning_setup(monkeypatch) -> None:
    """Revert first, then setup.

    Setup routinely writes untracked files into a repo; `git clean` running after
    it would wipe the very state setup just recreated, returning the box to the
    image's state rather than the sample's.
    """
    calls: list[str] = []

    class FakeBox:
        async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
            calls.append(cmd[-1])
            return SimpleNamespace(success=True, stdout="", stderr="")

    async def fake_setup(script):
        calls.append(f"setup:{script}")

    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: ["benchmark"])
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: FakeBox())
    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", fake_setup)

    asyncio.run(restore_benchmark("echo setup"))

    # a liveness probe fires first, then the git revert, then setup
    assert len(calls) == 3
    assert calls[0] == "true"
    assert "git" in calls[1] and "reset" in calls[1]
    assert calls[2] == "setup:echo setup"


def test_restore_raises_on_an_unreachable_box_so_the_caller_can_rebuild(monkeypatch) -> None:
    """A soft reset runs inside the box; a dead box must raise, not report success.

    Otherwise an image-baked benchmark (no setup script) would git-noop, setup-noop
    and falsely claim a bricked box was restored -- the caller's phoenix fallback
    never fires. The liveness probe returning non-success is the brick signal.
    """

    class DeadBox:
        async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
            return SimpleNamespace(success=False, stdout="", stderr="no such container")

    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: ["benchmark"])
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: DeadBox())

    with pytest.raises(RuntimeError, match="unreachable"):
        asyncio.run(restore_benchmark(None))  # script=None: the case that used to slip


def test_restore_raises_when_the_revert_itself_cannot_run(monkeypatch) -> None:
    """A live box without bash/git must not report a restore it never did.

    The revert script ends `; true`, so a non-success exit means the box could
    not even run it -- exactly the image-baked case where a silent no-op used
    to read as "restored".
    """

    class NoBashBox:
        async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
            if cmd == ["true"]:
                return SimpleNamespace(success=True, stdout="", stderr="")
            return SimpleNamespace(success=False, stdout="", stderr="bash: not found")

    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: ["benchmark"])
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: NoBashBox())

    with pytest.raises(RuntimeError, match="could not run"):
        asyncio.run(restore_benchmark(None))


def test_restore_reverts_every_benchmark_box(monkeypatch) -> None:
    """Every box is per-sample state, so every box gets reverted.

    A CTF sibling restored to nothing would grade against a victim box still
    carrying the last attempt's damage.
    """
    reverted: list[str] = []

    def make_box(name: str):
        class Box:
            async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
                if "git" in cmd[-1]:
                    reverted.append(name)
                return SimpleNamespace(success=True, stdout="", stderr="")

        return Box()

    async def fake_setup(script):
        pass

    monkeypatch.setattr(
        sandbox_module, "benchmark_boxes", lambda: ["benchmark", "victim"]
    )
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: make_box(name))
    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", fake_setup)

    asyncio.run(restore_benchmark(None))

    assert reverted == ["benchmark", "victim"]


def test_setup_replay_injects_a_shebang_and_resolves_the_source(monkeypatch) -> None:
    """Mirror inspect's sample-init: resolve the source, add a shebang if absent.

    The runner execs the file directly (no shell), so a shebang-less script --
    the common case -- would run under sh or not at all. A data-URI source must
    also resolve, which pre-reading the text at construction dropped.
    """
    import base64

    from inspect_audit import _sandbox as sb

    captured: dict = {}

    async def fake_setup_runner(setup_bytes, environments):
        captured["bytes"] = setup_bytes

    monkeypatch.setattr(sb, "has_benchmark_box", lambda: True)
    monkeypatch.setattr(sb, "sandbox_env", lambda name=None: object())
    monkeypatch.setattr(sb, "setup_sandbox_environment", fake_setup_runner)

    # plain shell, no shebang -> a bash shebang is injected
    asyncio.run(sb.run_benchmark_setup("echo [[ $x == y ]]"))
    assert captured["bytes"].decode().startswith("#!/usr/bin/env bash\n")
    assert "echo [[ $x == y ]]" in captured["bytes"].decode()

    # an existing shebang is left as-is
    asyncio.run(sb.run_benchmark_setup("#!/bin/sh\necho hi"))
    assert captured["bytes"].decode() == "#!/bin/sh\necho hi"

    # a data-URI source resolves to its decoded bytes (then gets a shebang)
    uri = "data:text/plain;base64," + base64.b64encode(b"echo from-uri").decode()
    asyncio.run(sb.run_benchmark_setup(uri))
    assert "echo from-uri" in captured["bytes"].decode()


def test_soft_reset_reports_the_repos_it_reset(monkeypatch) -> None:
    """Restore returns what it reset, so a no-op is visible.

    A repo-backed benchmark whose worktree was never found must read as
    'reset nothing', not a silent success.
    """

    class Box:
        async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
            if "git" in cmd[-1]:
                return SimpleNamespace(success=True, stdout="reset /repo/a\nreset /repo/b\n", stderr="")
            return SimpleNamespace(success=True, stdout="", stderr="")

    async def fake_setup(script):
        pass

    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: ["benchmark"])
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: Box())
    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", fake_setup)

    reset = asyncio.run(restore_benchmark("echo hi"))
    assert reset == ["benchmark:/repo/a", "benchmark:/repo/b"]


def test_soft_reset_reports_nothing_when_no_repo_is_found(monkeypatch) -> None:
    """No git output -> the receipt shows it reset nothing (the look-here signal)."""

    class Box:
        async def exec(self, cmd, timeout=None, input=None):  # noqa: D102
            return SimpleNamespace(success=True, stdout="", stderr="")

    async def fake_setup(script):
        pass

    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: ["benchmark"])
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: Box())
    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", fake_setup)

    assert asyncio.run(restore_benchmark(None)) == []


def test_benchmark_boxes_reads_the_live_environment_set(tmp_path: Path) -> None:
    """Unmocked: inside a real eval, a box-less sample has no benchmark boxes.

    Every guard rests on this function, and every other test mocks it -- so
    this one runs it for real, where `sandbox("benchmark")` would happily
    return the auditor's own environment.
    """
    from inspect_ai import eval as inspect_eval
    from inspect_ai.solver import Generate, TaskState, solver

    from inspect_audit._sandbox import benchmark_boxes as real_boxes
    from inspect_audit._sandbox import has_benchmark_box as real_has

    seen: dict = {}

    @solver
    def peek():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            seen["boxes"] = real_boxes()
            seen["has"] = real_has()
            return state

        return solve

    log = inspect_eval(
        Task(
            name="peek",
            dataset=MemoryDataset([Sample(id=1, input="q", target="a")]),
            solver=peek(),
            sandbox="local",
            scorer=match(),
        ),
        model="mockllm/model",
        log_dir=str(tmp_path),
        display="none",
    )[0]

    assert log.status == "success", log.error
    assert seen["boxes"] == []
    assert seen["has"] is False


def test_benchmark_boxes_filters_only_the_auditor() -> None:
    """Whatever topology the sample runs, every non-auditor box is listed."""
    from inspect_ai.util._sandbox.context import sandbox_environments_context_var

    from inspect_audit._sandbox import benchmark_boxes, has_benchmark_box

    token = sandbox_environments_context_var.set(
        {"default": object(), "benchmark": object(), "victim": object()}  # type: ignore[arg-type]
    )
    try:
        assert benchmark_boxes() == ["benchmark", "victim"]
        assert has_benchmark_box() is True
    finally:
        sandbox_environments_context_var.reset(token)


def test_restore_refuses_without_a_benchmark_box(monkeypatch) -> None:
    """A box-less item's soft reset must refuse, not act on the auditor.

    `sandbox("benchmark")` falls back to the DEFAULT env on a one-environment
    sample, so without this guard the reset would git-wipe the auditor's own
    container and report success.
    """
    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: [])

    with pytest.raises(RuntimeError, match="no benchmark box"):
        asyncio.run(restore_benchmark("echo setup"))


def _patch_phoenix(
    monkeypatch,
    *,
    services: dict,
    running: list[str],
    exited_ok: list[str] = (),
    is_docker: bool = True,
    setup_error: str | None = None,
) -> dict:
    """Wire phoenix_benchmark's compose calls to fakes; return a record of them."""
    calls: dict = {"setup": []}
    project = SimpleNamespace(name="proj", config="/x/compose.yaml", env={})

    class FakeDockerEnv:
        # a proxy-like env: as_type unwraps to a docker env carrying the project
        _project = project

        def as_type(self, _cls):
            return self

    class NonDockerEnv:
        def as_type(self, _cls):
            raise TypeError("not a docker sandbox")

    box = FakeDockerEnv() if is_docker else NonDockerEnv()
    monkeypatch.setattr(sandbox_module, "sandbox_env", lambda name=None: box)

    async def fake_services(project):
        return services

    async def fake_command(cmd, *, project=None, timeout=None, timeout_retry=True):
        calls["cmd"] = cmd
        return SimpleNamespace(success=True, stdout="", stderr="boom")

    async def fake_ps(project=None, status=None, all=False):
        # compose_ps returns the whole project; the auditor `default` is always up.
        # branch on status the way the real one does, so the exit-0 path is exercised.
        calls.setdefault("ps_status", []).append(status)
        if status == "running":
            return [{"Service": name} for name in ["default", *running]]
        if status == "exited":
            return [{"Service": name, "ExitCode": 0} for name in exited_ok]
        return []

    async def fake_setup(script):
        calls["setup"].append(script)
        if setup_error is not None:
            raise RuntimeError(setup_error)

    # the seeding path re-lays files only into LIVE boxes; the live set is the
    # running services plus any that exited 0 (a one-shot writer counts as up)
    live = [*running, *exited_ok]
    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: live)
    monkeypatch.setattr(sandbox_module, "compose_services", fake_services)
    monkeypatch.setattr(sandbox_module, "compose_command", fake_command)
    monkeypatch.setattr(sandbox_module, "compose_ps", fake_ps)
    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", fake_setup)
    return calls


def test_phoenix_force_recreates_only_the_benchmark_service(monkeypatch) -> None:
    """The rebuild names the benchmark service and never touches the auditor."""
    calls = _patch_phoenix(
        monkeypatch, services={"default": {}, "benchmark": {}}, running=["benchmark"]
    )

    summary = asyncio.run(phoenix_benchmark("echo setup"))

    cmd = calls["cmd"]
    assert "up" in cmd and "--force-recreate" in cmd
    assert "benchmark" in cmd
    assert "default" not in cmd  # the auditor is never recreated
    assert "down" not in cmd  # never project-wide
    assert "--no-deps" not in cmd  # naming the set replaces the need for it
    assert calls["setup"] == ["echo setup"]  # per-sample state repopulated after
    assert "rebuilt from image" in summary


def test_phoenix_recreates_every_non_auditor_service(monkeypatch) -> None:
    """CTF-style siblings (victim, writer) a brick can take with it are rebuilt too."""
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}, "victim": {}},
        running=["benchmark", "victim"],
    )

    asyncio.run(phoenix_benchmark(None))

    cmd = calls["cmd"]
    assert "benchmark" in cmd and "victim" in cmd
    assert "default" not in cmd


def test_phoenix_raises_when_the_box_does_not_come_back(monkeypatch) -> None:
    """A brick that reached past the container leaves nothing running -- a finding."""
    _patch_phoenix(
        monkeypatch, services={"default": {}, "benchmark": {}}, running=[]
    )

    with pytest.raises(RuntimeError, match="did not come back"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_timeout_is_not_a_brick_if_the_box_is_up(monkeypatch) -> None:
    """A slow-but-healthy rebuild must not be misreported as a brick.

    The recreate may finish while only the health-wait lags, so a host TimeoutError
    defers to the state check -- which finds the box up and re-seeds normally.
    """
    calls = _patch_phoenix(
        monkeypatch, services={"default": {}, "benchmark": {}}, running=["benchmark"]
    )

    async def slow_command(cmd, *, project=None, timeout=None, timeout_retry=True):
        calls["cmd"] = cmd
        raise TimeoutError("health-wait lagged")

    monkeypatch.setattr(sandbox_module, "compose_command", slow_command)

    summary = asyncio.run(phoenix_benchmark("echo setup"))
    assert "rebuilt from image" in summary  # up per compose_ps, not a brick


def test_phoenix_timeout_with_the_box_still_down_is_a_brick(monkeypatch) -> None:
    """But a timeout AND nothing running is a genuine brick finding."""
    calls = _patch_phoenix(monkeypatch, services={"default": {}, "benchmark": {}}, running=[])

    async def slow_command(cmd, *, project=None, timeout=None, timeout_retry=True):
        calls["cmd"] = cmd
        raise TimeoutError("hung")

    monkeypatch.setattr(sandbox_module, "compose_command", slow_command)

    with pytest.raises(RuntimeError, match="timed out and the box is still not up"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_refuses_to_reseed_a_file_for_an_exited_service(monkeypatch) -> None:
    """A one-shot service that exited is not a live box.

    Re-laying a file it targets would raise or mis-route to the auditor, so
    refuse explicitly.
    """
    # writer exited 0 (so the up-check passes) but a one-shot exited service is
    # NOT in inspect's live-environments dict, so benchmark_boxes excludes it
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}, "writer": {}},
        running=["benchmark"],
        exited_ok=["writer"],
    )
    monkeypatch.setattr(sandbox_module, "benchmark_boxes", lambda: ["benchmark"])
    assert calls  # (recreate targets still include writer)

    with pytest.raises(RuntimeError, match="no live box by that name"):
        asyncio.run(
            phoenix_benchmark(None, {"writer:/seed.txt": "/host/seed.txt"})
        )


def test_phoenix_is_unsupported_off_docker(monkeypatch) -> None:
    _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}},
        running=["benchmark"],
        is_docker=False,
    )

    with pytest.raises(RuntimeError, match="needs the docker sandbox"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_reports_state_a_rebuild_cannot_reset(monkeypatch) -> None:
    """Named volumes / bind-mounts survive force-recreate; say so, don't imply clean."""
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {"volumes": ["vol:/data"]}},
        running=["benchmark"],
    )

    summary = asyncio.run(phoenix_benchmark("echo setup"))

    assert "volumes" in summary and "benchmark" in summary
    assert calls["cmd"]  # still recreated


def test_phoenix_needs_a_benchmark_box(monkeypatch) -> None:
    _patch_phoenix(monkeypatch, services={"default": {}}, running=[])

    with pytest.raises(RuntimeError, match="no benchmark box"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_surfaces_a_setup_failure_as_itself(monkeypatch) -> None:
    """A setup failure on the fresh box is reported as itself, not 'did not come back'.

    The box did come back; re-seeding it is what failed, and the message says so.
    """
    _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}},
        running=["benchmark"],
        setup_error="Benchmark setup failed: boom",
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        asyncio.run(phoenix_benchmark("echo setup"))


def test_phoenix_counts_a_clean_one_shot_service_as_up(monkeypatch) -> None:
    """A CTF-style writer that plants state and exits 0 is up, not a failed rebuild.

    Checking only status=running would flag such a sibling as "did not come back"
    and raise a spurious brick finding on a healthy box, so exited-code-0 counts too.
    """
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}, "writer": {}},
        running=["benchmark"],
        exited_ok=["writer"],
    )

    summary = asyncio.run(phoenix_benchmark("echo setup"))

    assert "rebuilt from image" in summary  # writer's clean exit did not trip it
    assert "exited" in calls["ps_status"]  # the exit-0 path was actually consulted


def test_phoenix_relays_sample_files_then_runs_setup(monkeypatch, tmp_path: Path) -> None:
    """A rebuilt box is empty, so file-delivered per-sample state must be re-laid.

    Through the REAL resolve/read/copy routines: the `service:` prefix must
    route each file to its own box (a sibling's fixture back to the sibling), a
    directory value must expand recursively, and nothing may land on the
    auditor. Mocking the routing away is how a mis-keyed relay once shipped.
    """
    calls = _patch_phoenix(
        monkeypatch,
        services={"default": {}, "benchmark": {}, "victim": {}},
        running=["benchmark", "victim"],
    )
    order: list[str] = []
    writes: dict[str, dict[str, bytes]] = {}

    class FakeEnv:
        def __init__(self, name: str) -> None:
            self.name = name

        async def write_file(self, file: str, contents: bytes) -> None:
            if not order or order[-1] != "files":
                order.append("files")
            writes.setdefault(self.name, {})[file] = contents

    envs = {name: FakeEnv(name) for name in ("default", "benchmark", "victim")}
    # phoenix resolves the auditor as sandbox_env() and each target by name;
    # _patch_phoenix's box fake is replaced by name-aware envs for this test
    monkeypatch.setattr(
        sandbox_module, "sandbox_env", lambda name="default": envs[name]
    )
    # keep the docker unwrap working: as_type must still return the project
    envs["default"].as_type = lambda _cls: SimpleNamespace(_project=SimpleNamespace(name="proj", config="/x/compose.yaml", env={}))  # type: ignore[attr-defined]

    async def ordered_setup(script):
        order.append("setup")
        calls["setup"].append(script)

    monkeypatch.setattr(sandbox_module, "run_benchmark_setup", ordered_setup)

    given = tmp_path / "given.txt"
    given.write_bytes(b"fixture-bytes")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.txt").write_bytes(b"aa")
    (bundle / "b.txt").write_bytes(b"bb")

    # the real copy resolves its ambient default through this context var,
    # which inspect sets during a sample; set it the way a live sample has it
    from inspect_ai.util._sandbox.context import sandbox_default_context_var

    reset_var = sandbox_default_context_var.set("default")

    asyncio.run(
        phoenix_benchmark(
            "echo setup",
            {
                "benchmark:/work/given.txt": str(given),
                "victim:/flag.txt": str(given),
                "benchmark:/work/bundle": str(bundle),
            },
        )
    )

    sandbox_default_context_var.reset(reset_var)

    # prefix stripped, bytes intact, directory expanded, sibling routed
    assert writes["benchmark"]["/work/given.txt"] == b"fixture-bytes"
    assert writes["victim"]["/flag.txt"] == b"fixture-bytes"
    assert writes["benchmark"]["/work/bundle/a.txt"] == b"aa"
    assert writes["benchmark"]["/work/bundle/b.txt"] == b"bb"
    assert "default" not in writes  # nothing lands on the auditor
    assert order == ["files", "setup"]  # files first, then setup


def test_a_dockerfile_environment_is_reproduced_not_dropped(tmp_path: Path) -> None:
    """A Dockerfile sandbox becomes a benchmark service, matching inspect's own run.

    Inspect's auto-generated compose for a Dockerfile builds it and runs it with
    `network_mode: none`; an audit that silently dropped that box would audit the
    question without the environment the attempts actually ran in.
    """
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n")
    spec = SandboxEnvironmentSpec("docker", str(dockerfile))
    assert has_benchmark(spec)

    sandbox = audit_compose(make_task(), spec, stage=tmp_path / "stage")
    services = yaml.safe_load(Path(str(sandbox[1])).read_text())["services"]

    assert set(services) == {"default", "benchmark"}
    assert services["benchmark"]["build"] == {
        "context": str(tmp_path),
        "dockerfile": "Dockerfile",
    }
    # isolation parity: the eval ran the box with no network, so must the audit
    assert services["benchmark"]["network_mode"] == "none"


def test_a_bare_docker_environment_is_reproduced(tmp_path: Path) -> None:
    """`sandbox="docker"` ran the generic tool-support image; the audit reruns it."""
    spec = SandboxEnvironmentSpec("docker")
    assert has_benchmark(spec)

    sandbox = audit_compose(make_task(), spec, stage=tmp_path / "stage")
    services = yaml.safe_load(Path(str(sandbox[1])).read_text())["services"]

    assert set(services) == {"default", "benchmark"}
    assert services["benchmark"]["image"] == "aisiuk/inspect-tool-support"
    assert services["benchmark"]["network_mode"] == "none"


def test_an_unreproducible_environment_warns_rather_than_silently_dropping(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    spec = SandboxEnvironmentSpec("docker", str(tmp_path / "missing-compose.yaml"))
    assert not has_benchmark(spec)

    with caplog.at_level(logging.WARNING, logger="inspect_audit._sandbox"):
        sandbox = audit_compose(make_task(), spec, stage=tmp_path / "stage")

    assert "without the benchmark environment" in caplog.text
    # the auditor still stands alone rather than failing the audit outright
    assert Path(str(sandbox[1])).name == "compose.yaml"


def test_no_declared_environment_stands_alone_without_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="inspect_audit._sandbox"):
        audit_compose(make_task(), None, stage=tmp_path / "stage")
    assert caplog.text == ""


def _merged_services(tmp_path: Path, services: dict) -> dict:
    compose = tmp_path / "compose.yaml"
    compose.write_text(yaml.safe_dump({"services": services}))
    spec = SandboxEnvironmentSpec("docker", str(compose))
    _, out = audit_compose(make_task(), spec, stage=tmp_path / "stage")
    return yaml.safe_load(Path(out).read_text())["services"]


def test_a_benchmark_service_named_default_cannot_replace_the_auditor(
    tmp_path: Path,
) -> None:
    """x-default elsewhere + a service literally named `default` is legal compose.

    Un-renamed, that `default` overwrites the auditor in the merge: no audit
    cell at all, and inspect stages the audit's own files -- sliced logs, gold
    answers -- into the benchmark's container.
    """
    merged = _merged_services(
        tmp_path,
        {"web": {"image": "i", "x-default": True}, "default": {"image": "victim"}},
    )

    # the auditor holds `default` (it builds; the interloper's image does not),
    # their x-default service became `benchmark`, their `default` moved aside
    assert "build" in merged["default"]
    assert merged["default"].get("x-default") is True
    assert merged["benchmark"]["image"] == "i"
    assert merged["default_"]["image"] == "victim"
    # no benchmark service may carry a default claim into the merge
    assert not any(
        svc.get("x-default") for name, svc in merged.items() if name != "default"
    )


def test_an_interloping_benchmark_service_moves_aside(tmp_path: Path) -> None:
    """Their unrelated service named `benchmark` must not shadow the real one.

    Every hardcoded address -- reset, grade, probe, mirrored tools -- targets
    `benchmark`, so the audited default must end up under that name and the
    interloper somewhere else.
    """
    merged = _merged_services(
        tmp_path,
        {
            "benchmark": {"image": "interloper"},
            "web": {"image": "the-real-one", "x-default": True},
            "helper": {"image": "h", "depends_on": ["web", "benchmark"]},
        },
    )

    assert merged["benchmark"]["image"] == "the-real-one"
    assert merged["benchmark_"]["image"] == "interloper"
    # references follow both renames
    assert merged["helper"]["depends_on"] == ["benchmark", "benchmark_"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("90s", 90),
        ("1m30s", 90),
        ("2h", 7200),
        ("500ms", 1),  # sub-second rounds up, never to zero
        ("1.5s", 2),
        ("0s", None),  # a zero duration drops the field (k8s rejects 0)
        ("500us", 1),  # units the old regex silently dropped
        ("junk", None),
        ("", None),  # empty duration -> field dropped, not periodSeconds: 0
    ],
)
def test_seconds_parses_compose_durations(value: str, expected: int | None) -> None:
    assert _seconds(value) == expected


def test_task_pins_follow_the_install_source(monkeypatch) -> None:  # noqa: ANN001
    """A git-installed task package pins to its commit; a local install is skipped."""
    import json

    from inspect_audit import _sandbox

    class Dist:
        def __init__(self, direct):  # noqa: ANN001
            self.direct = direct

        def read_text(self, name: str):  # noqa: ANN202
            return json.dumps(self.direct) if self.direct is not None else None

    cases = {
        "from-pypi": (None, None),
        "from-git": (
            {"url": "https://github.com/o/r", "vcs_info": {"vcs": "git", "commit_id": "abc123"}},
            "from-git @ git+https://github.com/o/r@abc123",
        ),
        "editable": ({"url": "file:///home/me/pkg", "dir_info": {"editable": True}}, ""),
    }
    for dist, (direct, expected) in cases.items():
        monkeypatch.setattr(
            "importlib.metadata.distribution", lambda name, d=direct: Dist(d)
        )
        assert _sandbox._direct_url_requirement(dist) == expected


def test_the_container_templates_are_files_that_render() -> None:
    """A Dockerfile kept as a Python string is invisible to every tool that reads one."""
    from inspect_audit import containers

    assert (containers.HERE / "auditor.Dockerfile").is_file()
    assert (containers.HERE / "auditor.compose.yaml").is_file()
    assert (containers.HERE / "egress.helm.yaml").is_file()

    rendered = containers.DOCKERFILE.format(requirements="inspect_evals==1.0")
    assert rendered.startswith("#") and "FROM python:" in rendered
    assert "inspect_evals==1.0" in rendered and "{" not in rendered
    # a line continuation must be one backslash: two is a literal, and docker build
    # fails on it. This is what four docker tests caught when the file was extracted.
    assert "\\\\" not in rendered
    for line in rendered.splitlines():
        assert not line.rstrip().endswith("\\\\"), line

    compose = yaml.safe_load(containers.COMPOSE)
    assert compose["services"]["default"]["network_mode"] == "bridge"
    assert compose["services"]["default"]["build"]["dockerfile"] == "Dockerfile"

    # the egress policy is Helm-templated YAML: it is not valid YAML on its own, but it
    # must stay a single `additionalResources` block that names the auditor's service
    assert containers.EGRESS_POLICY.startswith("additionalResources:")
    assert "inspect/service: default" in containers.EGRESS_POLICY
    assert "kube-dns" in containers.EGRESS_POLICY


@pytest.mark.parametrize('combined', [False, True])
def test_git_requirement_is_one_shell_argument(tmp_path, monkeypatch, combined) -> None:
    import shlex
    requirement = 'inspect_evals @ git+https://github.com/org/inspect_evals@abc123'
    monkeypatch.setattr(sandbox_module, 'task_requirements', lambda task: [requirement])
    if combined:
        _, config = audit_compose(make_task(), None, stage=tmp_path / 'stage')
    else:
        _, config = sandbox_module.audit_sandbox(make_task())
    dockerfile = (Path(config).parent / 'Dockerfile').read_text()
    command = next(line[4:] for line in dockerfile.splitlines() if line.startswith('RUN pip install'))
    assert requirement in shlex.split(command)
    assert '@' not in shlex.split(command)
