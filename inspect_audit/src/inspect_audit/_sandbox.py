import atexit
import json
import math
import os
import re
import shlex
import shutil
import tempfile
from importlib.metadata import PackageNotFoundError, packages_distributions, version
from logging import getLogger
from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Task
from inspect_ai._eval.task.sandbox import read_sandboxenv_file, resolve_sample_files
from inspect_ai._eval.task.util import task_run_dir
from inspect_ai.dataset import Sample
from inspect_ai.util import (
    SandboxEnvironmentSpec,
    SandboxEnvironmentType,
    is_dockerfile,
    subprocess,
)
from inspect_ai.util import sandbox as sandbox_env
from inspect_ai.util._sandbox.compose import DOCKERFILE as INSPECT_DOCKERFILE
from inspect_ai.util._sandbox.context import (
    copy_sandbox_environment_files,
    setup_sandbox_environment,
)
from inspect_ai.util._sandbox.docker.compose import (
    COMPOSE_WAIT,
    compose_command,
    compose_ps,
    compose_services,
)

# reuse Inspect's own auto-compose templates rather than re-deriving them, so a
# synthesised benchmark box matches what `inspect eval` would have run -- the
# same coupling to private core the task loader takes on (see `_resolve`)
from inspect_ai.util._sandbox.docker.config import (
    COMPOSE_DOCKERFILE_YAML,
    COMPOSE_GENERIC_YAML,
)
from inspect_ai.util._sandbox.docker.docker import DockerSandboxEnvironment
from inspect_ai.util._sandbox.docker.service import (
    parse_duration,
    services_healthcheck_time,
)
from inspect_ai.util._sandbox.environment import resolve_sandbox_environment

from .containers import COMPOSE, DOCKERFILE, EGRESS_POLICY

logger = getLogger(__name__)

BENCHMARK_SERVICE = "benchmark"

# a healthcheck time is a schedule, not a recreate budget: stop + remove + create
# + start take real time on top of the health-wait. this is the slack the host
# timeout adds over compose's own --wait-timeout before it may call a rebuild dead.
_RECREATE_OVERHEAD = 60


def benchmark_boxes() -> list[str]:
    """Every benchmark container live in this sample, by service name.

    The benchmark's default runs as `benchmark`; its siblings keep their own
    names. Whatever topology the audited compose declared, this is the set of
    boxes an auditor may reach. Read from the live environment dict --
    `sandbox(name)` cannot answer membership, because inspect resolves a
    *named* lookup to the default environment whenever the sample has exactly
    one, so on a box-less item it silently hands back the auditor's own box.
    """
    from inspect_ai.util._sandbox.context import sandbox_environments_context_var

    environments = sandbox_environments_context_var.get(None) or {}
    return [name for name in environments if name != AUDITOR_SERVICE_NAME]


def has_benchmark_box() -> bool:
    """Whether this sample's project actually includes a benchmark service."""
    return BENCHMARK_SERVICE in benchmark_boxes()


async def run_benchmark_setup(script: str | None) -> None:
    """Run the audited sample's setup in the benchmark service.

    The setup is what populates a benchmark's per-sample state; benchmarks whose
    state is baked into a per-sample image (e.g. SWE-bench) carry no setup.
    Replayed exactly as inspect's own sample-init does (sandbox.py:136-143 +
    context.py): resolve the source (file path, data URI, http, or literal
    text), inject a `#!/usr/bin/env bash` shebang when the script has none, then
    run it through the setup runner (write to a file, `chmod +x`, exec via
    `env`) so `INSPECT_SANDBOX_SETUP_TIMEOUT` applies. The runner execs the file
    directly, not through a shell, so a shebang-less script would otherwise run
    under `sh` (bashisms fail) or not at all -- `bash -c` here diverged from
    what the audited eval ran, and dropping the shebang injection diverged again.
    """
    if not script:
        return
    if not has_benchmark_box():
        # distinguish "no box configured" from "the box didn't come up": the
        # audit builder already decided this item HAS a benchmark, so an absent
        # live box here means the container failed to start, not a box-less item
        raise RuntimeError(
            "the benchmark box is not running -- it failed to start or exited, "
            "so its per-sample setup cannot be run"
        )
    setup_bytes = await read_sandboxenv_file(script)
    setup_str = setup_bytes.decode("utf-8")
    if not setup_str.strip().startswith("#!"):
        setup_str = f"#!/usr/bin/env bash\n\n{setup_str}"
    # the runner executes in the dict's default environment; hand it the
    # benchmark box under that name
    await setup_sandbox_environment(
        setup_str.encode("utf-8"), {AUDITOR_SERVICE_NAME: sandbox_env(BENCHMARK_SERVICE)}
    )


# return every git worktree in the box to HEAD and drop untracked files (but keep
# ignored build artifacts) -- how an image-baked repo returns to pristine. reset
# --hard, not checkout, because a prior grade stages its changes (git add -A) and
# checkout would only revert the working tree back to that staged, patched state
# echo each repo it actually reset, so a no-op is visible in the receipt rather
# than inferred: `; true` at the end keeps the exec success (a repo with nothing
# to reset is not a failure), but a repo that was never found -- deeper than the
# search depth, on a different filesystem -- simply prints nothing and the caller
# can see it did nothing. `-c safe.directory=*` defeats git's dubious-ownership
# refusal when the checkout is owned by a different uid than the exec user, which
# would otherwise no-op silently under the `2>/dev/null`.
_GIT_RESTORE_DEPTH = 6
_GIT_RESTORE = (
    f"for g in $(find / -maxdepth {_GIT_RESTORE_DEPTH} -type d -name .git 2>/dev/null); do "
    'r=$(dirname "$g"); '
    'git -c safe.directory="*" -C "$r" reset --hard --quiet 2>/dev/null && '
    'git -c safe.directory="*" -C "$r" clean -fdq 2>/dev/null && '
    'echo "reset $r"; done; true'
)


async def restore_benchmark(script: str | None) -> list[str]:
    """Restore the benchmark service to its pristine per-sample state.

    Reverts every git worktree in the box first, then re-runs any setup -- in
    that order, because setup routinely writes untracked files into a repo and
    `git clean` after it would wipe the very state setup just recreated. A
    benchmark whose state comes from setup and one whose state is a checked-out
    repo both return to where the evaluated agent started.

    Probes the box first and raises if it is unreachable: this is the *soft*
    path, executing inside the live container, so a bricked box cannot be
    restored here (its own git/setup execs would run in a corpse). Raising lets
    the caller fall back to a rebuild -- important for image-baked benchmarks
    with no setup script, where the git/setup steps would otherwise no-op and
    falsely report success on a dead box.

    Returns the worktrees it actually reset, per box (`<box>:<path>`), so a
    reset that found no repos -- a checkout deeper than the search, an ownership
    refusal -- is visible in the receipt rather than a silent no-op reported as
    success. An empty list on a repo-backed benchmark is the signal to look.
    """
    boxes = benchmark_boxes()
    if not boxes:
        raise RuntimeError("this item has no benchmark box to restore")
    reset_repos: list[str] = []
    # every box, not just the primary: a CTF sibling holds per-sample state too
    for name in boxes:
        box = sandbox_env(name)
        alive = await box.exec(["true"], timeout=30)
        if not alive.success:
            raise RuntimeError(f"benchmark box {name!r} is unreachable; a rebuild is needed")
        restored = await box.exec(["bash", "-c", _GIT_RESTORE], timeout=300)
        if not restored.success:
            # the script itself ends `; true`, so a failure here means the box
            # could not even run it (no bash, a corrupted root) -- claiming
            # "restored" would be false success on exactly the image-baked case
            # this exists for
            raise RuntimeError(
                f"the in-place restore could not run in benchmark box {name!r}: "
                f"{restored.stderr[:500]}"
            )
        reset_repos += [
            f"{name}:{line.removeprefix('reset ').strip()}"
            for line in restored.stdout.splitlines()
            if line.startswith("reset ")
        ]
    await run_benchmark_setup(script)
    return reset_repos


# the auditor rides here; every other service is the benchmark's and gets rebuilt
AUDITOR_SERVICE_NAME = "default"


async def phoenix_benchmark(
    script: str | None, files: dict[str, str] | None = None
) -> str:
    """Rebuild the benchmark box from its image, then repopulate per-sample state.

    Where `restore_benchmark` reverts filesystem state *inside a live box*, this
    brings a box back from the dead -- a killed PID 1, a corrupted or filled root,
    a hang -- by force-recreating its container(s) from the image. Every service
    but the auditor `default` is recreated: CTF-style benchmarks carry siblings
    (`victim`, `writer`) a brick can take with it, and the auditor is no benchmark
    service's dependency, so naming the benchmark set never restarts it. Docker
    only -- the recreate is the docker provider's; the k8s path is its own.

    A rebuilt container is empty, so both channels the harness used to seed the
    box are replayed, in the order sample-init used: the sample's `files` are
    copied back in (via Inspect's own resolve/read/copy routines, honouring the
    `service:path` prefix), then any setup script runs. Restoring only setup
    would silently drop file-delivered state -- a fixture, a data file -- that
    the evaluated agent started with.

    Args:
        script: The sample's setup script, re-run in the fresh box.
        files: The sample's `Sample.files`, keyed as `service:path` (the audit
            carries these on `benchmark_files`); re-copied into the fresh box.

    Raises:
        RuntimeError: the provider is not docker, the item has no benchmark box, or
            the box did not come back (the brick reached past the container -- the
            daemon, the host disk, or a shared resource -- which is itself a finding).
    """
    # the default service is the auditor; it holds the project shared by every box.
    # `sandbox()` hands back a proxy, so unwrap to the concrete docker env with
    # as_type (which also type-checks) rather than isinstance against the proxy
    env = sandbox_env()
    try:
        docker = env.as_type(DockerSandboxEnvironment)
    except TypeError:
        raise RuntimeError(
            "phoenix reset needs the docker sandbox; this run's provider is not "
            "docker, where rebuilding a bricked box from its image is unsupported."
        ) from None
    project = docker._project

    services = await compose_services(project)
    targets = [name for name in services if name != AUDITOR_SERVICE_NAME]
    if not targets:
        raise RuntimeError("this item has no benchmark box to rebuild.")

    # size the wait to the services' healthchecks exactly as inspect's own
    # compose_up does, so a box with a long healthcheck is not killed prematurely.
    # but a healthcheck time is a *schedule*, not a recreate budget -- stop,
    # remove, create and start take real time on top of it -- so add a recreate
    # overhead the host timeout must clear before it can call a slow rebuild a brick
    wait = services_healthcheck_time({t: services[t] for t in targets}) or COMPOSE_WAIT

    # force-recreate the benchmark service(s) from image -- a fresh container fs is
    # the only way back from a brick. name them explicitly and never `compose down`,
    # which is project-wide and would take the auditor and its volumes with it.
    # timeout_retry=False: a genuine daemon hang should fail fast as a finding, not
    # retry for ~11 minutes holding a docker-cli slot.
    timed_out = False
    result = None
    try:
        # host timeout sits ABOVE compose's own --wait-timeout: with retries off
        # (unlike inspect's compose_up, which tolerates the race by retrying), a
        # host timeout that fired first would misreport a slow-but-healthy
        # rebuild as a brick that reached past the container
        result = await compose_command(
            [
                "up",
                "--detach",
                "--wait",
                "--wait-timeout",
                str(wait + 1),
                "--force-recreate",
                *targets,
            ],
            project=project,
            timeout=wait + _RECREATE_OVERHEAD,
            timeout_retry=False,
        )
    except TimeoutError:
        # a timeout is not itself a verdict -- the recreate may have finished and
        # only the health-wait lagged. fall through and let the state check below
        # decide, exactly as inspect's own compose_up defers to compose_ps.
        timed_out = True
    # `--wait` returns non-zero for services that exit even successfully, so
    # verify by state, not exit code. check the targets directly rather than
    # reusing compose_check_running: it counts against the whole project (the
    # auditor keeps running), so a subset always trips its count guard. a
    # target that legitimately exits 0 (a one-shot writer/init) is up too --
    # match inspect's own success notion or a CTF sibling false-alarms.
    running = {s["Service"] for s in await compose_ps(project=project, status="running")}
    exited_ok = {
        s["Service"]
        for s in await compose_ps(project=project, status="exited")
        if s.get("ExitCode") == 0
    }
    down = [t for t in targets if t not in running | exited_ok]
    if down:
        detail = (
            "the rebuild timed out and the box is still not up"
            if timed_out
            else (result.stderr or "")[:500] if result is not None else ""
        )
        raise RuntimeError(
            f"the benchmark box did not come back after a rebuild ({', '.join(down)}) "
            "-- the brick reached past the container (the daemon, the host disk, or a "
            f"shared resource): {detail}"
        )

    # replay both seeding channels into the empty box(es), files then setup (as
    # sample-init does). files are prefixed `service:path`; hand the copy each
    # LIVE box by name -- a sibling-targeted fixture must land back on the
    # sibling -- plus the ambient default (the auditor) the way inspect's own
    # init resolves it. only live boxes: a one-shot service that exited (a
    # writer that plants state and stops) is not in the environments dict, so
    # `sandbox_env(name)` for it would raise or mis-resolve to the auditor.
    if files:
        live = set(benchmark_boxes())
        wanted = {
            file.split(":", 1)[0] for file in files if ":" in file
        }
        unreachable = wanted - live - {AUDITOR_SERVICE_NAME}
        if unreachable:
            raise RuntimeError(
                f"cannot re-lay files for {', '.join(sorted(unreachable))}: no live "
                "box by that name after the rebuild (a one-shot service that exited "
                "cannot be re-seeded from here)"
            )
        resolved = resolve_sample_files(files)
        contents = {path: await read_sandboxenv_file(src) for path, src in resolved.items()}
        environments = {AUDITOR_SERVICE_NAME: sandbox_env()}
        for name in live:
            environments[name] = sandbox_env(name)
        await copy_sandbox_environment_files(contents, environments)
    await run_benchmark_setup(script)

    caveat = await _unreset_mounts(services, targets)
    rebuilt = f"benchmark box rebuilt from image ({', '.join(targets)})"
    return f"{rebuilt}; {caveat}" if caveat else rebuilt


async def _unreset_mounts(services: dict[str, Any], targets: list[str]) -> str | None:
    """Name any target whose state survives a rebuild, so nobody reads it as clean.

    Three persistence vectors outlive `--force-recreate`, because it rebuilds the
    container from the image but not the storage under it: compose-declared named
    volumes and host bind-mounts (the `volumes:` key), and volumes the *image*
    declares (`VOLUME /data` in the Dockerfile -- common in db images), whose anon
    volume compose migrates onto the new container. State written to any of them is
    not reset, so "rebuilt from image" would otherwise overclaim.
    """
    mounted = {t for t in targets if services.get(t, {}).get("volumes")}
    imaged = set()
    for t in targets:
        if await _image_declares_volumes(services.get(t, {})):
            imaged.add(t)
    flagged = sorted(mounted | imaged)
    if not flagged:
        return None
    return (
        f"volumes/bind-mounts on {', '.join(flagged)} are not reset by a rebuild "
        "(they live outside the container), so state written there persists"
    )


async def _image_declares_volumes(service: dict[str, Any]) -> bool:
    """Whether a service's image declares its own VOLUMEs (best-effort).

    Only a named image can be inspected cheaply; a build-only service has no image
    to query pre-build, so it is treated as declaring none (any VOLUME in its
    Dockerfile is a gap this does not close). Any docker error is swallowed -- the
    caveat is advisory, never a reason to fail a rebuild.
    """
    image = service.get("image")
    if not isinstance(image, str) or not image:
        return False
    try:
        # bounded: this runs right after a rebuild, possibly against the very
        # degraded daemon phoenix exists to survive
        result = await subprocess(
            ["docker", "image", "inspect", image, "--format", "{{json .Config.Volumes}}"],
            timeout=60,
        )
    except Exception:
        return False
    if not result.success:
        return False
    declared = result.stdout.strip()
    return bool(declared) and declared not in ("null", "{}")



AUDITOR_SERVICE: dict[str, Any] = {
    "build": {"context": None, "dockerfile": "Dockerfile"},
    "command": "sleep infinity",
    "init": True,
    # an `x-default` on any service beats one merely named `default`, so the
    # auditor must claim it or a benchmark declaring its own would receive the
    # audit's files, sliced logs and answers included
    "x-default": True,
    # egress via the shared bridge, zero per-sample networks (see COMPOSE above)
    "network_mode": "bridge",
    "stop_grace_period": "1s",
}


def compose_renames(spec: SandboxEnvironmentType | None) -> dict[str, str]:
    """How the audited environment's service names map into the merged project.

    Sample `files` and setup carry these names as prefixes, so anything staging
    them must rewrite through the same mapping the compose merge applies.
    """
    found = benchmark_source(resolve_sandbox_environment(spec))
    if found is None:
        return {}
    return _service_renames(found[0].get("services") or {})


def _service_renames(services: dict[str, Any]) -> dict[str, str]:
    """How the audited compose's service names map into the merged project.

    Three reserved-name rules keep the two-box world addressable, whatever the
    audited compose declares:

    - the benchmark's own default service becomes `benchmark`, so setup, grade,
      reset and the mirrored tools have a stable address for it;
    - any *other* service squatting on `benchmark` moves aside first, or every
      one of those addresses would silently hit the interloper;
    - any service named `default` that is not the benchmark's default moves
      aside too: `default` is the auditor's slot, and left in place it would
      replace the auditor in the merge -- no audit cell at all, and inspect
      would stage the audit's own files (sliced logs, answers) into the
      benchmark's container.
    """
    default_service = _default_service(services)
    if default_service is None:
        return {}
    renames: dict[str, str] = {}
    taken = set(services) | {BENCHMARK_SERVICE, AUDITOR_SERVICE_NAME}

    def uniquify(name: str) -> str:
        while name in taken:
            name += "_"
        taken.add(name)
        return name

    if BENCHMARK_SERVICE in services and BENCHMARK_SERVICE != default_service:
        renames[BENCHMARK_SERVICE] = uniquify(BENCHMARK_SERVICE)
    renames[default_service] = BENCHMARK_SERVICE
    if AUDITOR_SERVICE_NAME in services and AUDITOR_SERVICE_NAME != default_service:
        renames[AUDITOR_SERVICE_NAME] = uniquify(AUDITOR_SERVICE_NAME)
    return renames


def _default_service(services: dict[str, Any]) -> str | None:
    """The benchmark's default service, by inspect's own precedence.

    An `x-default: true` service wins over one merely named `default`; failing
    both, a lone service is the default. `None` when it is genuinely ambiguous
    (several services, none marked) -- the caller renames nothing.
    """
    for name, svc in services.items():
        if isinstance(svc, dict) and svc.get("x-default"):
            return name
    if "default" in services:
        return "default"
    if len(services) == 1:
        return next(iter(services))
    return None


def task_requirements(task: Task) -> list[str]:
    """Pinned pip requirements that provide the audited task."""
    # the task's registry package plus each scorer's package
    modules: set[str] = set()
    if "/" in task.name:
        modules.add(task.name.split("/")[0])
    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    for scorer in scorers:
        if scorer is None:
            continue
        fn = getattr(scorer, "__wrapped__", scorer)
        module = getattr(fn, "__module__", "")
        if module:
            modules.add(module.split(".")[0])
    modules = {m for m in modules if m and m.isidentifier()}

    # pin to the versions in the environment the audit resolved the task in.
    # a task package that is not on PyPI (installed from git, as Hawk installs
    # `packages:`) is pinned to the exact commit it came from via its PEP 610
    # direct_url.json -- `name==version` would send pip to PyPI for a package
    # that is not there and fail the image build. an editable or local-path
    # install cannot be reproduced inside the image at all: skip it, loudly.
    pins: dict[str, str] = {}

    def pin(dist: str) -> None:
        try:
            dist_version = version(dist)
        except PackageNotFoundError:
            return
        requirement = _direct_url_requirement(dist)
        if requirement is None:
            pins[dist] = f"{dist}=={dist_version}"
        elif requirement:
            pins[dist] = requirement
        else:
            logger.warning(
                f"{dist} is installed from a local path, which the auditor image "
                "cannot reproduce; the auditor runs without the task's package"
            )

    pin("inspect-ai")
    distributions = packages_distributions()
    for module in modules:
        for dist in distributions.get(module, []):
            pin(dist)

    return [pins[dist] for dist in sorted(pins)]


def _direct_url_requirement(dist: str) -> str | None:
    """A PEP 508 requirement for a non-PyPI install, per PEP 610.

    `None` for a normal index install (pin by version), the empty string for an
    editable or local-directory install (unreproducible), else `name @ vcs+url@commit`.
    """
    from importlib.metadata import distribution

    text = distribution(dist).read_text("direct_url.json")
    if not text:
        return None
    direct = json.loads(text)
    url = str(direct.get("url", ""))
    if "vcs_info" in direct:
        vcs = direct["vcs_info"]
        ref = vcs.get("commit_id") or vcs.get("requested_revision")
        pinned = f"{vcs['vcs']}+{url}" + (f"@{ref}" if ref else "")
        subdirectory = direct.get("subdirectory")
        if subdirectory:
            pinned += f"#subdirectory={subdirectory}"
        return f"{dist} @ {pinned}"
    if "archive_info" in direct and not url.startswith("file:"):
        return f"{dist} @ {url}"
    return ""


def audit_sandbox(task: Task) -> SandboxEnvironmentType:
    """A sandbox for auditing `task`, with the task's own packages installed."""
    out = Path(tempfile.mkdtemp(prefix="inspect_audit_sandbox_"))
    if not os.environ.get("INSPECT_AUDIT_KEEP_STAGING"):
        atexit.register(shutil.rmtree, out, ignore_errors=True)
    (out / "Dockerfile").write_text(
        DOCKERFILE.format(requirements=shlex.join(task_requirements(task)))
    )
    compose = out / "compose.yaml"
    compose.write_text(COMPOSE)
    return ("docker", str(compose))


def benchmark_source(
    spec: SandboxEnvironmentSpec | None,
) -> tuple[dict[str, Any], Path | None] | None:
    """The audited environment as a compose document, with its anchor directory.

    A compose file is read as written. A Dockerfile config or a bare `docker`
    provider carries no compose file, so it is synthesised from Inspect's own
    auto-compose templates -- the same YAML `inspect eval` would have generated
    for that sandbox, `network_mode: none` and all -- rather than reconstructed
    here, so the benchmark box tracks whatever Inspect runs.

    Args:
        spec: The sandbox the audited sample runs in, or `None`.

    Returns:
        The compose document and the directory its relative paths resolve
        against, or `None` when there is no environment to reproduce. The anchor
        is `None` when the document holds no paths needing to be re-anchored.
    """
    if spec is None:
        return None
    config = spec.config
    if isinstance(config, str):
        source = Path(config)
        if not source.is_file():
            return None
        if is_dockerfile(source.name):
            # Inspect's Dockerfile template builds `./Dockerfile`; point it at the
            # audited task's actual Dockerfile, absolute so no anchoring is needed
            merged = yaml.safe_load(
                COMPOSE_DOCKERFILE_YAML.format(dockerfile=INSPECT_DOCKERFILE)
            )
            merged["services"]["default"]["build"] = {
                "context": str(source.parent),
                "dockerfile": source.name,
            }
            return merged, None
        return dict(yaml.safe_load(source.read_text()) or {}), source.parent
    if config is None and spec.type == "docker":
        # a bare `docker` sandbox runs Inspect's generic container
        return yaml.safe_load(COMPOSE_GENERIC_YAML), None
    return None


def has_benchmark(spec: SandboxEnvironmentSpec | None) -> bool:
    """Whether the audited task's environment runs as a benchmark service."""
    return benchmark_source(spec) is not None


def sample_sandbox(task: Task, sample: Sample) -> SandboxEnvironmentSpec | None:
    """The sandbox an audited sample actually runs in (its own, else its task's)."""
    # resolve_sandbox already falls back to the task's sandbox for a bare sample
    return resolve_sandbox(task, sample.sandbox)


def resolve_sandbox(
    task: Task, sandbox: SandboxEnvironmentType | None = None
) -> SandboxEnvironmentSpec | None:
    """The audited task's own sandbox, with any relative config path made absolute."""
    spec = resolve_sandbox_environment(sandbox if sandbox is not None else task.sandbox)
    if spec is None:
        return None
    # inspect resolves relative config paths against the running task's directory,
    # which is the audit's, not the audited task's
    if isinstance(spec.config, str) and not Path(spec.config).is_absolute():
        return SandboxEnvironmentSpec(
            spec.type, (Path(task_run_dir(task)) / spec.config).as_posix()
        )
    return spec


def audit_compose(
    task: Task, spec: SandboxEnvironmentSpec | None, *, stage: Path
) -> SandboxEnvironmentType:
    """A sandbox holding the audited task's environment and the auditor's, side by side.

    The audited task's services are preserved as declared, except that a service
    named `default` is renamed: inspect treats that name as an alias for the default
    environment rather than a service lookup, so it would be unaddressable.

    Args:
        task: The task being audited.
        spec: The sandbox the audited sample runs in, or `None`.
        stage: Directory to write the merged compose and the auditor's Dockerfile into.
    """
    found = benchmark_source(spec)
    if found is None:
        # nothing to reproduce: the auditor's environment alone. loud when the
        # audited task declared an environment we could not reproduce -- an audit
        # of what an environment affords is not valid without the environment.
        if spec is not None:
            logger.warning(
                f"cannot reproduce the audited sandbox ({spec.type!r}, config "
                f"{spec.config!r}); the auditor runs without the benchmark environment"
            )
        return audit_sandbox(task)
    merged, base = found
    services: dict[str, Any] = merged.get("services") or {}
    renames = _service_renames(services)

    # re-anchor relative paths and strip any x-default claims
    renamed = {}
    for name, service in services.items():
        anchored = _anchor_service(service, base) if base is not None else dict(service)
        anchored.pop("x-default", None)
        renamed[renames.get(name, name)] = anchored

    # rewrite depends_on references to renamed services
    for service in renamed.values():
        depends = service.get("depends_on")
        if isinstance(depends, list):
            service["depends_on"] = [renames.get(d, d) for d in depends]
        elif isinstance(depends, dict):
            service["depends_on"] = {renames.get(k, k): v for k, v in depends.items()}

    # add the auditor service; it rides the shared bridge, so no per-sample
    # network is allocated. the benchmark's own networks (if any) are preserved.
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "Dockerfile").write_text(
        DOCKERFILE.format(requirements=shlex.join(task_requirements(task)))
    )
    auditor = dict(AUDITOR_SERVICE)
    auditor["build"] = {"context": str(stage), "dockerfile": "Dockerfile"}
    merged["services"] = {"default": auditor, **renamed}

    out = stage / "compose.yaml"
    out.write_text(yaml.safe_dump(merged, sort_keys=False))
    return ("docker", str(out))


# paths inside a compose file are relative to that file's directory, and the
# merged file lives somewhere else

def _anchor(value: Any, base: Path) -> Any:
    if isinstance(value, str) and not value.startswith(("/", "$")) and ":" not in value:
        return str((base / value).resolve())
    return value


def _anchor_service(service: dict[str, Any], base: Path) -> dict[str, Any]:
    out = dict(service)
    build = out.get("build")
    if isinstance(build, str):
        out["build"] = _anchor(build, base)
    elif isinstance(build, dict):
        build = dict(build)
        if "context" in build:
            build["context"] = _anchor(build["context"], base)
        out["build"] = build
    for key in ("env_file", "dockerfile"):
        if isinstance(out.get(key), str):
            out[key] = _anchor(out[key], base)
        elif isinstance(out.get(key), list):
            out[key] = [_anchor(v, base) for v in out[key]]
    if isinstance(out.get("volumes"), list):
        out["volumes"] = [_anchor_volume(v, base) for v in out["volumes"]]
    return out


def _anchor_volume(volume: Any, base: Path) -> Any:
    # both bind-mount syntaxes: a missed relative source is created empty by docker
    # and the benchmark container silently loses the data mounted there
    if isinstance(volume, str) and volume.startswith("."):
        host, _, rest = volume.partition(":")
        return f"{_anchor(host, base)}:{rest}"
    if isinstance(volume, dict) and volume.get("type") == "bind":
        bound = dict(volume)
        bound["source"] = _anchor(bound.get("source"), base)
        return bound
    return volume



_MEMORY = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>gb?|mb?|kb?|b)$", re.IGNORECASE)
_MEMORY_UNITS = {"b": "", "k": "Ki", "m": "Mi", "g": "Gi"}


def _as_list(value: str | list[str]) -> list[str]:
    return value.split() if isinstance(value, str) else value


def _seconds(value: Any) -> int | None:
    # inspect's own compose-duration parser (it knows ns/us/µs and embedded
    # whitespace, and errors on garbage a regex would silently skip over).
    # probe fields are whole seconds >= 1: a zero or empty duration returns
    # None so the field is dropped rather than emitting periodSeconds: 0, which
    # k8s rejects; a positive sub-second value rounds up.
    try:
        total = parse_duration(str(value)).seconds
    except ValueError:
        return None
    return math.ceil(total) if total > 0 else None


def _quantity(value: Any) -> Any:
    # `512m` -> `512Mi`; unrecognised values pass through for the chart to reject.
    match = _MEMORY.match(str(value))
    if match is None:
        return value
    return f"{match.group('value')}{_MEMORY_UNITS[match.group('unit')[0].lower()]}"


def _env(value: dict[str, Any] | list[str]) -> list[dict[str, str]]:
    if isinstance(value, dict):
        return [{"name": k, "value": "" if v is None else str(v)} for k, v in value.items()]
    return [{"name": k, "value": v} for k, _, v in (item.partition("=") for item in value)]


def _readiness_probe(src: dict[str, Any], name: str) -> dict[str, Any] | None:
    test = src.get("test")
    if not isinstance(test, list) or test[:1] not in (["CMD"], ["CMD-SHELL"]):
        logger.warning(f"dropping 'healthcheck' from service '{name}': only CMD and CMD-SHELL tests convert")
        return None
    command = test[1:] if test[0] == "CMD" else ["sh", "-c", test[1]]
    probe: dict[str, Any] = {"exec": {"command": command}}
    for key, target in (
        ("start_period", "initialDelaySeconds"),
        ("interval", "periodSeconds"),
        ("timeout", "timeoutSeconds"),
    ):
        if key in src and (seconds := _seconds(src[key])) is not None:
            probe[target] = seconds
    if isinstance(src.get("retries"), int):
        # N retries is a failureThreshold of N+1.
        probe["failureThreshold"] = src["retries"] + 1
    return probe


def _resources(src: dict[str, Any]) -> dict[str, Any]:
    declared = (src.pop("deploy", None) or {}).get("resources") or {}

    def section(block: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if cpus := block.get("cpus"):
            out["cpu"] = cpus
        if memory := block.get("memory"):
            out["memory"] = _quantity(memory)
        return out

    limits = section(declared.get("limits") or {})
    requests = section(declared.get("reservations") or {})
    if mem_limit := src.pop("mem_limit", None):
        limits.setdefault("memory", _quantity(mem_limit))
    if cpus := src.pop("cpus", None):
        limits.setdefault("cpu", cpus)
    resources: dict[str, Any] = {}
    if limits:
        resources["limits"] = limits
        # As the chart's own converter does: requests default to limits for QoS.
        resources["requests"] = {**limits, **requests}
    elif requests:
        resources["requests"] = requests
    return resources


def _security_context(user: Any) -> dict[str, Any]:
    uid, _, gid = str(user).partition(":")
    context: dict[str, Any] = {"runAsUser": int(uid)}
    if gid:
        context["runAsGroup"] = int(gid)
    return context


def _values_service(name: str, service: dict[str, Any], benchmark_image: str | None) -> dict[str, Any]:
    """Convert one compose service to a chart service, dropping what k8s cannot express."""
    src = dict(service)
    out: dict[str, Any] = {}
    build = src.pop("build", None)
    if "image" in src:
        out["image"] = src.pop("image")
    elif build is not None:
        if benchmark_image is None:
            raise ValueError(
                f"service '{name}' is defined by 'build:', which k8s does not support "
                "-- images must be pullable. Pass benchmark_image naming a published image."
            )
        out["image"] = benchmark_image
    if build is not None:
        logger.warning(f"dropping 'build' from service '{name}': k8s images must be pullable")
    # compose entrypoint maps to the chart's command (the container's argv[0]);
    # compose command maps to its args -- unless there is no entrypoint, in which
    # case compose command IS the process to run, so it must be the chart command
    # or the image's own entrypoint runs instead and a `sleep infinity` never fires
    if "entrypoint" in src:
        out["command"] = _as_list(src.pop("entrypoint"))
        if "command" in src:
            out["args"] = _as_list(src.pop("command"))
    elif "command" in src:
        out["command"] = _as_list(src.pop("command"))
    if "working_dir" in src:
        out["workingDir"] = src.pop("working_dir")
    # A DNS record for every service, matching Docker Compose's name resolution.
    out["dnsRecord"] = True
    if "environment" in src:
        out["env"] = _env(src.pop("environment"))
    if "volumes" in src:
        out["volumes"] = src.pop("volumes")
    if "healthcheck" in src:
        if (probe := _readiness_probe(src.pop("healthcheck"), name)) is not None:
            out["readinessProbe"] = probe
    if resources := _resources(src):
        out["resources"] = resources
    if "user" in src:
        out["securityContext"] = _security_context(src.pop("user"))
    if "networks" in src:
        out["networks"] = src.pop("networks")
    # Everything left -- init, privileged, extra_hosts, stop_grace_period, x-default,
    # depends_on, ... -- has no chart equivalent and is dropped rather than passed
    # through to fail schema validation.
    for key in sorted(src):
        logger.warning(f"dropping '{key}' from service '{name}': no k8s equivalent")
    return out


def audit_values(
    task: Task,
    spec: SandboxEnvironmentSpec | None,
    *,
    stage: Path,
    auditor_image: str,
    benchmark_image: str | None = None,
) -> SandboxEnvironmentType:
    """The same audit sandbox as `audit_compose`, emitted as Helm values for k8s.

    On Kubernetes providers (METR's Hawk) the sandbox is a Helm chart, `build:` is
    unsupported, and egress is Cilium deny-by-default. So the auditor's image must be
    published rather than built, and its egress is granted by a per-service network
    policy appended to the values file -- no sandbox-wide `allowDomains`/`allowEntities`
    grants, so the benchmark's services stay as isolated as they were during the eval.

    Args:
        task: The task being audited.
        spec: The sandbox the audited sample runs in, or `None`.
        stage: Directory to write the values file into.
        auditor_image: Published image to run the auditor's service from.
        benchmark_image: Published image standing in for a `build:`-based benchmark
            service. Required if any of their services is built rather than pulled.

    Returns:
        A `("k8s", <values.yaml path>)` sandbox spec.
    """
    values: dict[str, Any] = {}
    converted: dict[str, Any] = {}

    found = benchmark_source(spec)
    if found is None and spec is not None:
        logger.warning(
            f"cannot reproduce the audited sandbox ({spec.type!r}, config "
            f"{spec.config!r}); the auditor runs without the benchmark environment"
        )
    if found is not None:
        compose, _ = found
        theirs: dict[str, Any] = compose.get("services") or {}
        renames = _service_renames(theirs)
        for name, service in theirs.items():
            converted[renames.get(name, name)] = _values_service(
                name, service, benchmark_image
            )
        if volumes := compose.get("volumes"):
            values["volumes"] = {name: {} for name in volumes}
        if networks := compose.get("networks"):
            values["networks"] = {name: {} for name in networks}

    auditor = {"image": auditor_image, "command": ["sleep", "infinity"], "dnsRecord": True}
    values = {"services": {"default": auditor, **converted}, **values}

    stage.mkdir(parents=True, exist_ok=True)
    # Not named `compose.yaml`: the k8s provider reads Helm values, not a compose file.
    out = stage / "values.yaml"
    out.write_text(yaml.safe_dump(values, sort_keys=False) + EGRESS_POLICY)
    return ("k8s", str(out))
