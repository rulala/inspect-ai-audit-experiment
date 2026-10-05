import json
import os
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task, task, task_with
from inspect_ai.dataset import MemoryDataset
from inspect_ai.log import list_eval_logs
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

from ._agent import grade_benchmark, reset_benchmark
from ._audit import audit_task
from ._concordance import probe_concordance
from ._investigate import investigate as investigate
from ._item import AUDIT_ROOT
from ._legacy_report import report_task
from ._resolve import resolve_task, resolve_task_from_log
from ._sandbox import (
    BENCHMARK_SERVICE,
    has_benchmark,
    has_benchmark_box,
    sample_sandbox,
)


@task
def audit(
    task: str | None = None,
    task_args: dict[str, object] | None = None,
    logs: str | None = None,
    samples: list[str] | None = None,
    limit: int | None = None,
    items: list[str] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    notes: str | None = None,
    confidential: bool = False,
    redact: list[str] | None = None,
    attempts_task: str | None = None,
    auditor_image: str | None = None,
    benchmark_image: str | None = None,
) -> Task:
    """Audit a benchmark task from its logs.

    Args:
        task: Task to audit (defaults to the task the logs record).
        task_args: Task arguments used to resolve the audited task.
        logs: Log file or directory of logs holding the recorded attempts.
        samples: Sample ids to audit (defaults to all, subject to `limit`).
        limit: Audit at most this many samples.
        items: Audit items to investigate (defaults to all of them).
        model: Model to audit with (defaults to the evaluated model).
        reasoning_effort: Reasoning effort for the auditor model, when it takes one.
        notes: A free-form operator steer inserted into the auditor's system prompt.
        confidential: The benchmark is unpublished -- instruct the auditor not to
            transmit item content off the box.
        redact: Further metadata keys to strip from the item the auditor reads.
        attempts_task: Task whose attempts to join, when the logs record a sibling
            variant of the audited task (e.g. no-tools attempts at a tools task).
        auditor_image: Published auditor image; switches to Helm-values emission
            for k8s providers.
        benchmark_image: Published image for benchmark services that `build:`.
    """
    if logs:
        logs = fetch_logs(logs)
    if task is None:
        if not logs:
            raise ValueError("Provide a task to audit, or logs recording one.")
        files = (
            [logs]
            if logs.endswith((".eval", ".json"))
            else [info.name for info in list_eval_logs(logs)]
        )
        if not files:
            raise ValueError(f"No logs found at {logs!r}.")
        target: str | Task = resolve_task_from_log(files[0])
    else:
        target = task

    return audit_task(
        target,
        logs,
        samples=samples,
        limit=limit,
        task_args=task_args,
        items=items,
        model=model,
        reasoning_effort=reasoning_effort,
        notes=notes,
        confidential=confidential,
        redact=redact,
        attempts_task=attempts_task,
        auditor_image=auditor_image,
        benchmark_image=benchmark_image,
    )


@task
def report(logs: str | None = None) -> Task:
    """Legacy conversational synthesis over completed audit logs.

    Use inspect_audit/investigate for autonomous investigation and publication.

    v0 is the conversational skeleton: launch with `--acp-server` and attach
    via `inspect acp` (or the web chat in frontend/) to work with it. See
    `_report.py` for the roadmap.

    Args:
        logs: Log file or directory of audit logs to synthesize over.
    """
    return report_task(logs)


@solver
def audit_probe() -> Solver:
    """Assert the audit sandbox was assembled correctly, without spending on a model.

    Checks the auditor's filesystem and egress, that the sliced logs open with the
    installed inspect_ai, and that the benchmark service is populated and isolated.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        checks: dict[str, str] = {}

        async def run(name: str, target: str | None, cmd: str) -> None:
            # `sandbox(name)` falls back to the default box when the sample has
            # only one, so a benchmark check on a box-less item would report the
            # auditor's own filesystem as the benchmark's
            if target is not None and not has_benchmark_box():
                checks[name] = "SKIP no benchmark box"
                return
            box = sandbox() if target is None else sandbox(target)
            try:
                r = await box.exec(["bash", "-c", cmd], timeout=120)
                checks[name] = (r.stdout or r.stderr).strip()[:200]
            except Exception as ex:
                checks[name] = f"EXCEPTION {type(ex).__name__}: {ex}"[:200]

        await run("auditor_fs", None, "ls /audit && ls /audit/logs | head -3")
        await run(
            "auditor_logs_open", None,
            "python -c \"from inspect_ai.log import list_eval_logs, read_eval_log; "
            "ls=list_eval_logs('/audit/logs'); print(len(ls), read_eval_log(ls[0].name, header_only=True).eval.task)\"",
        )
        await run("auditor_egress", None,
                  "python -c \"import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=10).status)\"")
        await run("benchmark_content", BENCHMARK_SERVICE, "pwd; ls / | head -8; ls 2>/dev/null | head -8")
        await run("benchmark_isolated", BENCHMARK_SERVICE, "ls /audit 2>&1 | head -1")
        await run("benchmark_egress", BENCHMARK_SERVICE,
                  "timeout 10 python -c \"import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=8).status)\" 2>&1 | tail -1 || echo BLOCKED")

        # grade/reset: grade the box pristine (expect fail), apply the sample's own
        # gold patch and grade again (expect pass), then reset and grade once more
        # (expect fail). proves the benchmark's grader runs against the box, that a
        # real solution is credited, and that reset returns the box to pristine.
        await _probe_grade(state, checks)

        # concordance: prove the resolution and the grade channel against the
        # logs -- replay recorded attempts and require our regrade to reproduce
        # their scores. writes a machine-readable artifact the orchestrator reads.
        await _probe_concordance(state, checks)

        state.store.set("probe", checks)
        state.output.completion = json.dumps(checks, indent=1)
        return state

    return solve


async def _probe_concordance(state: TaskState, checks: dict[str, str]) -> None:
    try:
        item = (state.metadata or {}).get("audit_item") or {}
        audited = item.get("task")
        if audited is None:
            checks["concordance"] = "SKIP no audited task recorded"
            return
        resolved = resolve_task(audited, item.get("task_args") or {})
        scorers = resolved.scorer if isinstance(resolved.scorer, list) else [resolved.scorer]
        scorers = [s for s in scorers if s is not None]
        if not scorers:
            checks["concordance"] = "SKIP no benchmark scorer"
            return
        has_box = has_benchmark(sample_sandbox(resolved, resolved.dataset[0]))
        report = await probe_concordance(state, scorers, has_box=has_box)
        checks["concordance"] = report.verdict
        checks["concordance_reasons"] = "; ".join(report.reasons)[:200]
        await sandbox().write_file(f"{AUDIT_ROOT}/concordance.json", report.to_json())
    except Exception as ex:
        checks["concordance"] = f"EXCEPTION {type(ex).__name__}: {ex}"[:200]


async def _probe_grade(state: TaskState, checks: dict[str, str]) -> None:
    async def grade_value(grade: Any) -> str:
        scores = json.loads(await grade(answer=""))["scores"]
        one = scores[0] if isinstance(scores, list) else scores
        return str(one.get("value"))

    try:
        item = (state.metadata or {}).get("audit_item") or {}
        audited = item.get("task")
        if audited is None:
            checks["grade"] = "SKIP no audited task recorded"
            return
        resolved = resolve_task(audited, item.get("task_args") or {})
        scorers = resolved.scorer if isinstance(resolved.scorer, list) else [resolved.scorer]
        scorers = [s for s in scorers if s is not None]
        if not scorers:
            checks["grade"] = "SKIP no benchmark scorer"
            return
        grade = grade_benchmark(scorers)

        checks["grade_pristine"] = await grade_value(grade)

        # inject the sample's gold solution and grade again -- git-patch benchmarks
        # only; other shapes just exercise the pristine grade above
        benchmark_md = (state.metadata or {}).get("benchmark_metadata") or {}
        patch = benchmark_md.get("patch")
        if not patch:
            checks["grade"] = "SKIP gold injection is git-patch only"
            return
        applied = await sandbox(BENCHMARK_SERVICE).exec(
            ["bash", "-c", "cd /testbed && git apply -"], input=patch
        )
        checks["gold_applied"] = "ok" if applied.success else f"FAILED {applied.stderr[:120]}"
        checks["grade_gold"] = await grade_value(grade)

        await reset_benchmark()(hard=False)
        checks["grade_reset"] = await grade_value(grade)
    except Exception as ex:
        checks["grade"] = f"EXCEPTION {type(ex).__name__}: {ex}"[:200]


def fetch_logs(logs: str) -> str:
    """Resolve a `logs` argument to something inspect can read locally.

    - `hawk:<eval-set-id>[,<id>...]` downloads an eval set from the Hawk warehouse.
    - `http(s)://...eval` downloads one log; `http(s)://...` anything else is read
      as a manifest of log URLs (one per line, or a CSV with a `logs`/`url`
      column), e.g. the public S3 listing a benchmark publisher hands out.
    - anything else (a path, an `s3://` dir inspect reads natively) passes through.

    Downloads happen in the process running the eval -- on Hawk that is the
    trusted runner, which has egress; the auditor's sandbox need not.
    """
    if logs.startswith("hawk:"):
        return _hawk_fetch(logs.removeprefix("hawk:"))
    if logs.startswith(("http://", "https://")):
        return _url_fetch(logs)
    return logs


def _url_fetch(url: str) -> str:
    import csv
    import io
    import urllib.request

    def read(u: str) -> bytes:
        with urllib.request.urlopen(u, timeout=600) as r:
            return bytes(r.read())

    if url.endswith((".eval", ".json")):
        urls = [url]
    else:
        text = read(url).decode("utf-8")
        if "," in text.splitlines()[0]:
            rows = list(csv.DictReader(io.StringIO(text)))
            column = next((c for c in ("logs", "log", "url") if rows and c in rows[0]), None)
            if column is None:
                raise ValueError(f"Log manifest {url!r} needs a `logs`/`log`/`url` column.")
            urls = [str(row[column]).strip() for row in rows if row.get(column)]
        else:
            urls = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not urls:
        raise ValueError(f"No log URLs found in manifest {url!r}.")

    fetched = Path(tempfile.mkdtemp(prefix="url_logs_"))
    for u in urls:
        name = Path(u.split("?", 1)[0]).name
        if not name.endswith((".eval", ".json")):
            raise ValueError(f"Manifest entry {u!r} does not name an .eval/.json log.")
        (fetched / name).write_bytes(read(u))
    return str(fetched)


def _hawk_fetch(eval_sets: str) -> str:
    """Download eval logs from the Hawk warehouse to a temporary directory.

    Talks to the Hawk API directly with the runner's own credentials -- the hawk
    CLI stores tokens in an OS keyring, which headless runner pods do not have.
    Requires HAWK_API_URL, plus either HAWK_ACCESS_TOKEN or the runner's token
    refresh environment (HAWK_TOKEN_REFRESH_URL, HAWK_TOKEN_REFRESH_CLIENT_ID,
    HAWK_REFRESH_TOKEN).
    """
    import urllib.parse
    import urllib.request

    api = os.environ["HAWK_API_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {_hawk_token()}"}

    def get_json(path: str) -> dict[str, Any]:
        req = urllib.request.Request(api + path, headers=headers)
        with urllib.request.urlopen(req, timeout=180) as r:
            return dict(json.load(r))

    def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            api + path,
            data=json.dumps(body).encode(),
            headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            return dict(json.load(r))

    fetched = Path(tempfile.mkdtemp(prefix="hawk_logs_"))
    sets = eval_sets.split(",")
    for eval_set in sets:
        files = get_json(f"/view/logs/logs?log_dir={urllib.parse.quote(eval_set)}")["files"]
        names = [f["name"] for f in files if str(f.get("name", "")).endswith(".eval")]
        if not names:
            raise ValueError(f"No .eval files found in Hawk eval set {eval_set!r}.")
        # several sets share one flat directory: prefix so same-named files
        # cannot silently overwrite each other
        prefix = f"{eval_set}_" if len(sets) > 1 else ""
        urls = post_json("/view/logs/log-download-urls", {"logs": names})["urls"]
        for item in urls:
            dest = fetched / f"{prefix}{Path(item['filename']).name}"
            with urllib.request.urlopen(item["url"], timeout=600) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
    return str(fetched)


def _hawk_token() -> str:
    # a fresh token via the runner's refresh credentials, else the static one
    refresh_url = os.environ.get("HAWK_TOKEN_REFRESH_URL")
    refresh_token = os.environ.get("HAWK_REFRESH_TOKEN")
    client_id = os.environ.get("HAWK_TOKEN_REFRESH_CLIENT_ID")
    if refresh_url and refresh_token and client_id:
        import urllib.parse
        import urllib.request

        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
        ).encode()
        req = urllib.request.Request(
            refresh_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return str(json.load(r)["access_token"])
    token = os.environ.get("HAWK_ACCESS_TOKEN")
    if not token:
        raise ValueError(
            "Fetching hawk: logs needs HAWK_ACCESS_TOKEN or the runner's token "
            "refresh environment."
        )
    return token


@solver
def replay_exploit(exploits: dict[str, str]) -> Solver:
    """Run a recorded exploit's commands in the sample's box, as its whole solution.

    The exploit is keyed by sample id. Whatever it leaves in the box is graded by
    the task's own scorer exactly as a real submission would be -- so an exploit
    that passes here passes the genuine benchmark, not our harness.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        commands = exploits.get(str(state.sample_id))
        if commands:
            await sandbox().exec(["bash", "-c", commands], timeout=900)
        return state

    return solve


@task
def swebench_replay(
    exploits: dict[str, str],
    dataset: str = "princeton-nlp/SWE-bench_Verified",
    split: str = "test",
    allow_internet: bool = False,
    sandbox_type: str = "docker",
) -> Task:
    """Replay recorded exploits against the real SWE-bench task and grade them.

    Stage two of the exploit experiment: each sample runs its recorded exploit
    (from a red-teaming audit) as its solution, and the benchmark's own scorer
    grades the result. The pass rate is the real benchmark score of the exploits.

    Args:
        exploits: Map of sample id to the exact shell commands that carry out the
            exploit in that sample's box.
        dataset: SWE-bench dataset to draw the samples from.
        split: Dataset split.
        allow_internet: Whether the benchmark boxes may reach the internet.
        sandbox_type: Sandbox provider ("docker" locally, "k8s" on Hawk).
    """
    from inspect_evals.swe_bench import swe_bench

    base = swe_bench(
        dataset=dataset,
        split=split,
        allow_internet=allow_internet,
        sandbox_type=sandbox_type,
    )
    return replay_task(base, exploits)


def replay_task(base: Task, exploits: dict[str, str]) -> Task:
    """`base`, narrowed to the samples we hold an exploit for, solved by replay.

    Uses `task_with` rather than assigning attributes on the built task, which
    would skip Task's own normalisation of the dataset and solver. `task_with`
    edits `base` in place and returns it, so pass a task no other caller holds
    (`swebench_replay` builds a fresh one). The benchmark's scorer is untouched:
    it is the judge.
    """
    wanted = set(exploits)
    kept = [s for s in base.dataset if str(s.id) in wanted]
    return task_with(base, dataset=MemoryDataset(kept), solver=[replay_exploit(exploits)])
