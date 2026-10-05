"""Remote jobs: the policy over agent-written configs, staging, submission, ledger, collection."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from test_investigate import git_repo

from inspect_audit import _jobs
from inspect_audit._investigate import Remote, hawk_submit, jobs
from inspect_audit._jobs import (
    Job,
    JobLedger,
    Policy,
    task_package_name,
    validate_config,
)

TASK_PKG = "git+https://github.com/UKGovernmentBEIS/inspect_evals@abc"
AUDIT_PKG = "git+https://github.com/Generality-Labs/inspect_audit@def"
IMAGE = "ghcr.io/x/auditor@sha256:0"
HAWK = "https://hawk.example"
EXAMPLES = Path(__file__).parent.parent / "src/inspect_audit/investigation/skills/investigating/examples"


class FakeHawk:
    def __init__(self) -> None:
        self.submitted: list[Path] = []
        self.eval_status = "running"
        self.stopped: list[str] = []

    async def submit(self, config_path: Path) -> str:
        self.submitted.append(config_path)
        # real Hawk honours a pinned eval_set_id and echoes it back
        return str(yaml.safe_load(config_path.read_text())["eval_set_id"])

    async def evals(self, eval_set_id: str) -> list[dict[str, str]]:
        return [{"task": "t", "model": "m", "status": self.eval_status, "samples": "2/2"}]

    async def download(self, eval_set_id: str, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "run.eval"
        f.write_bytes(b"x")
        return [f]

    async def stop(self, eval_set_id: str) -> None:
        self.stopped.append(eval_set_id)

    async def watch(self, eval_set_id: str) -> str:
        return "sample 1: running, 2 retries\n⚠ pods can't be scheduled"

    async def trace(self, eval_set_id: str, lines: int = 100) -> str:
        return "enter generate ...\n"

    async def stacktrace(self, eval_set_id: str) -> str:
        return "Thread 1: asyncio ...\n"

    async def status(self, eval_set_id: str) -> str:
        return '{"pods": []}'

    async def samples(self, eval_set_id: str, limit: int | None = None) -> list[dict[str, object]]:
        # each eval set has its own samples; a uuid from another set is not in this list
        return [{"uuid": f"{eval_set_id}-s1", "id": "item-1", "epoch": 1, "status": "success", "scores": []}]

    async def has_sample(self, eval_set_id: str, sample_uuid: str) -> bool:
        return sample_uuid == f"{eval_set_id}-s1"

    async def logs(self, eval_set_id: str, lines: int = 120) -> str:
        return "uv pip install ... ok\nRunning Inspect eval-set"

    async def access_token(self) -> str:
        return "test-token"

    async def eval_set_exists(self, eval_set_id: str) -> bool:
        return any(
            str(yaml.safe_load(c.read_text())["eval_set_id"]) == eval_set_id
            for c in self.submitted
        )

    async def transcript(self, sample_uuid: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{sample_uuid}.md"
        path.write_text("# transcript")
        return path

    async def transcripts(self, eval_set_id: str, out_dir: Path, limit: int | None = None) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(limit or 2):
            f = out_dir / f"s-{i}.md"
            f.write_text("# transcript")
            paths.append(f)
        return paths


def remote(tmp_path: Path, allowance: float = 10.0) -> Remote:
    (tmp_path / "work").mkdir(exist_ok=True)
    r = Remote(tmp_path, HAWK, None, TASK_PKG, AUDIT_PKG, IMAGE,
               ["openai/gpt-5.6-luna", "openai/gpt-5-mini"], allowance)
    r.hawk = FakeHawk()  # type: ignore[assignment]
    return r


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def filled_example(filename: str, **overrides) -> dict:  # noqa: ANN003
    text = (EXAMPLES / filename).read_text()
    text = (
        text.replace("<remote.task_package>", TASK_PKG)
        .replace("<remote.audit_package>", AUDIT_PKG)
        .replace("<remote.auditor_image>", IMAGE)
        .replace("<remote.hawk>", HAWK)
        .replace("<registry package, e.g. inspect_evals>", "inspect_evals")
        .replace("<task, e.g. simpleqa_verified>", "simpleqa_verified")
        .replace("<registry name of the audited task, e.g. inspect_evals/simpleqa_verified>", "inspect_evals/simpleqa_verified")
        .replace("<remote.supplied_logs, or hawk:<eval set id of your job>>", "hawk:inv-staged-abc/inputs/logs")
    )
    config = yaml.safe_load(text)
    config.update(overrides)
    return config


def _price(qualified: str, input: float = 2.0, output: float = 10.0) -> None:
    """Register a price the way investigate() does, under the qualified model name."""
    from inspect_ai.model import ModelCost, ModelInfo, set_model_info

    set_model_info(
        qualified,
        ModelInfo(cost=ModelCost(input=input, output=output, input_cache_read=0.2, input_cache_write=2.5)),
    )


def policy() -> Policy:
    return Policy(packages=[TASK_PKG, AUDIT_PKG], task_names=["inspect_evals", "inspect_audit"],
                  models=["openai/gpt-5.6-luna", "openai/gpt-5-mini"], auditor_images=[IMAGE], hawk_api_url=HAWK)


def test_the_example_configs_pass_the_policy_once_filled_in() -> None:
    assert validate_config(filled_example("benchmark.eval-set.yaml"), policy(), set()) == []
    assert validate_config(filled_example("audit.eval-set.yaml"), policy(), {"inv-staged-abc"}) == []


@pytest.mark.parametrize(
    "change, expect",
    [
        ({"packages": ["git+https://evil/x"]}, "package not allowed"),
        ({"runner": {"image": "evil:v1", "environment": {"HAWK_API_URL": HAWK, "HAWK_RUNNER_REFRESH_URL": ""}, "secrets": [{"name": "OPENROUTER_API_KEY"}]}}, "runner keys not allowed"),
        ({"runner": {"environment": {"HAWK_API_URL": HAWK, "HAWK_RUNNER_REFRESH_URL": "", "AWS_SECRET": "x"}, "secrets": [{"name": "OPENROUTER_API_KEY"}]}}, "environment keys not allowed"),
        ({"runner": {"environment": {"HAWK_API_URL": HAWK, "HAWK_RUNNER_REFRESH_URL": ""}, "secrets": [{"name": "HF_TOKEN"}]}}, "secret not allowed"),
        ({"models": [{"package": "openai", "name": "openrouter", "items": [{"name": "openai/gpt-6-astra", "args": {"base_url": "https://openrouter.ai/api/v1"}}]}]}, "model not allowed"),
        ({"models": [{"package": "openai", "name": "openrouter", "items": [{"name": "openai/gpt-5.6-luna", "args": {"base_url": "https://evil/v1"}}]}]}, "base_url"),
        ({"agents": [{"package": "git+https://evil/a", "name": "a", "items": [{"name": "x"}]}]}, "keys not allowed"),
        ({"limit": 5000}, "must be a whole number from 1 to 1000"),
        ({"eval_set_id": "someone-elses"}, "remove eval_set_id"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"sandbox": "docker"}}]}]}, "task arg not allowed"),
        # the configuration that actually executes: a task argument, not the outer field
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"model": "openai/gpt-6-astra"}}]}]}, "is not an allowed model"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"grader_model": "openai/gpt-6-astra"}}]}]}, "is not an allowed model"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"benchmark_image": "ghcr.io/evil:latest"}}]}]}, "is not an allowed image"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"limit": 100000}}]}]}, "must be a whole number from 1 to"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "secrets": [{"name": "AWS_SECRET_ACCESS_KEY"}]}]}]}, "task-level secrets"),
        ({"tasks": [{"package": TASK_PKG, "name": "inspect_evals", "items": [{"name": "simpleqa_verified", "args": {"dataset": "s3://someone/else"}}]}]}, "points outside this investigation"),
        ({"secrets": [{"name": "AWS_SECRET_ACCESS_KEY"}]}, "top-level secrets are not allowed"),
        ({"limit": None}, "does not state its size"),
        ({"cost_limit": None}, "cost_limit is required"),
        ({"cost_limit": 500.0}, "cost_limit 500.0 must be"),
        ({"limit": 1000, "cost_limit": 5.0}, "exceeds the $200.00 a single job may hold"),
    ],
)
def test_policy_refuses_each_escape(change: dict, expect: str) -> None:
    problems = validate_config(filled_example("benchmark.eval-set.yaml", **change), policy(), set())
    assert any(expect in p for p in problems), problems


def test_policy_requires_a_size_and_known_log_sources() -> None:
    config = filled_example("benchmark.eval-set.yaml")
    del config["limit"]
    assert any("does not state its size" in p for p in validate_config(config, policy(), set()))
    # a size stated per item is a size
    config["tasks"][0]["items"][0]["sample_ids"] = ["a", "b"]
    assert validate_config(config, policy(), set()) == []
    audit = filled_example("audit.eval-set.yaml")
    assert any("staged or ran" in p for p in validate_config(audit, policy(), set()))
    assert validate_config(audit, policy(), {"inv-staged-abc"}) == []


def test_worst_case_is_cost_limit_times_the_work_the_config_asks_for() -> None:
    from inspect_audit._jobs import parse_config, worst_case_usd

    config = filled_example("benchmark.eval-set.yaml", limit=10, epochs=2, cost_limit=0.5)
    parsed, problems = parse_config(config)
    assert problems == []
    assert worst_case_usd(parsed, policy()) == 10.0  # 0.50 x 10 samples x 1 model x 2 epochs


def test_task_package_name() -> None:
    assert task_package_name(TASK_PKG) == "inspect_evals"
    assert task_package_name("inspect-evals==1.2") == "inspect_evals"
    assert task_package_name("git+https://github.com/x/epoch_bench.git@main") == "epoch_bench"


def _write(r_root: Path, name: str, config: dict) -> str:
    (r_root / "work" / "jobs").mkdir(parents=True, exist_ok=True)
    (r_root / "work" / "jobs" / name).write_text(yaml.safe_dump(config))
    return f"/workspace/jobs/{name}"


def test_submit_reserves_records_and_refuses_duplicates_and_overspend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    _price("openrouter/openai/gpt-5.6-luna")
    r = remote(tmp_path, allowance=3.0)
    path = _write(tmp_path, "smoke.eval-set.yaml", filled_example("benchmark.eval-set.yaml"))
    out = run(hawk_submit(r, tmp_path)(config=path, estimated_usd=0.3, note="smoke"))
    assert "Reserved $1.00" in out and "you estimated $0.30" in out
    job = JobLedger(tmp_path).get("smoke-luna")
    # the example runs 2 samples at $0.50 each: the hold is the worst case, not the guess
    assert job and job.estimated_usd == 0.3 and job.reserved_usd == 1.0 and job.status == "submitted"
    assert Path(job.config_path).is_file()
    assert job.eval_set_id in json.loads((tmp_path / "log_sources.json").read_text())
    submitted = yaml.safe_load(Path(job.config_path).read_text())
    assert submitted["eval_set_id"] == job.eval_set_id
    # keyed by the name the job runs under: provider group, then the model's own id
    assert submitted["model_cost_config"] == {
        "openrouter/openai/gpt-5.6-luna": {
            "input": 2.0,
            "output": 10.0,
            "input_cache_read": 0.2,
            "input_cache_write": 2.5,
        }
    }
    with pytest.raises(ToolError, match="already exists"):
        run(hawk_submit(r, tmp_path)(config=path, estimated_usd=0.5, note=None))
    other = _write(tmp_path, "two.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-two", limit=6))
    with pytest.raises(ToolError, match="cannot reserve"):
        run(hawk_submit(r, tmp_path)(config=other, estimated_usd=0.1, note=None))
    bad = _write(tmp_path, "bad.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-bad", packages=["git+https://evil/x"]))
    with pytest.raises(ToolError, match="config refused"):
        run(hawk_submit(r, tmp_path)(config=bad, estimated_usd=0.1, note=None))
    with pytest.raises(ToolError, match="under /workspace"):
        run(hawk_submit(r, tmp_path)(config="/etc/passwd", estimated_usd=0.1, note=None))
    assert len(r.hawk.submitted) == 1  # type: ignore[attr-defined]


def test_audit_over_supplied_logs_uses_the_staged_source_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    _price("openrouter/openai/gpt-5.6-luna")
    _price("openrouter/openai/gpt-5-mini", input=0.25, output=2.0)
    r.known_sources.add("inv-staged-abc")  # what investigate() records after staging at setup
    config = filled_example("audit.eval-set.yaml")
    assert "Submitted" in run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "audit.eval-set.yaml", config), estimated_usd=1.0, note=None))
    foreign = filled_example("audit.eval-set.yaml", name="inv-foreign")
    foreign["tasks"][0]["items"][0]["args"]["logs"] = "hawk:someone-elses-set"
    with pytest.raises(ToolError, match="staged or ran"):
        run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "f.eval-set.yaml", foreign), estimated_usd=1.0, note=None))


def test_local_logs_remain_local_when_remote_work_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local evidence is readable without inventing a remotely accessible prefix."""
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "t.py").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "t.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.org", "commit", "-qm", "c"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/org/bench.git"], check=True)
    log = tmp_path / "a.eval"
    log.write_bytes(b"x")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
    target = _investigate.investigate(
        str(repo), logs=[str(log)], output_dir=str(tmp_path / "runs"), enforce_cost_limit=False,
        hawk_api_url=HAWK, secrets_file=str(tmp_path / ".env"),
    )
    root = Path(target.metadata["investigation_dir"])
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["remote"]["supplied_logs"] == []
    assert (root / "inputs/logs/0/a.eval").read_bytes() == b"x"
    assert not (root / "staged.json").exists()
    assert "operator-imported Hawk source" in seed["remote"]["note"]
    assert target.setup is None



def test_jobs_status_wait_collect_release_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    monkeypatch.setattr(_investigate, "usage_cost", lambda files: (0.42, {"openrouter/m": {"input": 1, "cache_read": 2, "output": 3}}, False))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir()
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "j.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-jj")), estimated_usd=3, note=None))
    tool = jobs(r, tmp_path)
    assert "running" in run(tool(action="evals", label="jj", sample=None, wait_minutes=None, limit=None))
    assert "not finished" in run(tool(action="collect", label="jj", sample=None, wait_minutes=None, limit=None))
    r.hawk.eval_status = "success"  # type: ignore[attr-defined]
    monkeypatch.setattr(_jobs.time, "sleep", lambda s: None)
    assert "success" in run(tool(action="wait", label="jj", sample=None, wait_minutes=1, limit=None))
    out = run(tool(action="collect", label="jj", sample=None, wait_minutes=None, limit=None))
    assert "collected 1 log(s) to /inputs/jobs/jj/" in out and "$0.42" in out
    assert (tmp_path / "inputs" / "jobs" / "jj" / "run.eval").is_file()
    ledger = JobLedger(tmp_path)
    assert ledger.get("jj").actual_usd == 0.42 and ledger.reserved_usd() == 0  # type: ignore[union-attr]
    assert "jj (eval-set)" in run(tool(action="list", label=None, sample=None, wait_minutes=None, limit=None))
    run(tool(action="stop", label="jj", sample=None, wait_minutes=None, limit=None))
    assert r.hawk.stopped == [ledger.get("jj").eval_set_id]  # type: ignore[attr-defined,union-attr]
    assert "Running Inspect eval-set" in run(tool(action="logs", label="jj", sample=None, wait_minutes=None, limit=None))


def test_jobs_babysitting_actions_are_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """watch/trace/stacktrace/status/samples/transcripts observe a job, they do not change it."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir()
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "j.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-jj")), estimated_usd=3, note=None))
    tool = jobs(r, tmp_path)
    assert "pods can't be scheduled" in run(tool(action="watch", label="jj", sample=None, wait_minutes=None, limit=None))
    assert "enter generate" in run(tool(action="trace", label="jj", sample=None, wait_minutes=None, limit=None))
    assert "asyncio" in run(tool(action="stacktrace", label="jj", sample=None, wait_minutes=None, limit=None))
    assert "pods" in run(tool(action="status", label="jj", sample=None, wait_minutes=None, limit=None))
    mine = f"{JobLedger(tmp_path).get('jj').eval_set_id}-s1"  # type: ignore[union-attr]
    assert mine in run(tool(action="samples", label="jj", sample=None, wait_minutes=None, limit=None))
    with pytest.raises(ToolError, match="needs sample="):
        run(tool(action="transcript", label="jj", sample=None, wait_minutes=None, limit=None))
    # a uuid from another eval set: real Hawk would serve it, the tool must not
    with pytest.raises(ToolError, match="not in job"):
        run(tool(action="transcript", label="jj", sample="inv-someone-else-s1", wait_minutes=None, limit=None))
    assert f"{mine}.md" in run(tool(action="transcript", label="jj", sample=mine, wait_minutes=None, limit=None))
    assert (tmp_path / "inputs" / "jobs" / "jj" / "transcripts" / f"{mine}.md").is_file()
    assert "3 transcript(s)" in run(tool(action="transcripts", label="jj", sample=None, wait_minutes=None, limit=3))
    with pytest.raises(ToolError, match="action must be"):
        run(tool(action="delete", label="jj", sample=None, wait_minutes=None, limit=None))
    assert r.hawk.stopped == []  # type: ignore[attr-defined]
    assert JobLedger(tmp_path).get("jj").status == "submitted"  # type: ignore[union-attr]


async def _returns(value):  # noqa: ANN001, ANN202
    return value


def test_hawk_cli_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _jobs.Hawk("https://h", None)
    def returning(text: str):  # noqa: ANN202
        async def _run(*args: str, **kwargs: object) -> str:
            return text

        return _run

    monkeypatch.setattr(h, "_metadata_page", lambda *a: _returns([
        {"task_name": "audit/bench/Chess Puzzles", "model": "gpt-5.6-terra",
         "status": "success", "completed_samples": 10, "total_samples": 10}
    ]))
    assert run(h.evals("x")) == [{"task": "audit/bench/Chess Puzzles", "model": "gpt-5.6-terra", "status": "success", "samples": "10/10"}]
    monkeypatch.setattr(h, "_run", returning("Eval set ID: inv-abc-123\nSee your eval set log: https://..."))
    assert run(h.submit(Path("/tmp/c.yaml"))) == "inv-abc-123"
    # samples pages through the API rather than the CLI: the CLI cannot ask for page 2
    pages = [[{"id": "1", "status": "success"}], []]
    monkeypatch.setattr(h, "_samples_page", lambda *a, **k: _returns(pages.pop(0)))
    assert run(h.samples("x")) == [{"id": "1", "status": "success"}]

RESERVE_SCRIPT = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, {src!r})
from inspect_audit import _investigate
from inspect_audit._investigate import Remote
from inspect_audit._jobs import Job

root, label, amount, allowance, hold = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])

# _local_spend is called inside the locked transaction, so holding here holds the lock
def slow_spend():
    time.sleep(hold)
    return (0.0, [])

_investigate._local_spend = slow_spend
r = Remote(root, "https://hawk.example", None, "pkg", "pkg", "img", ["m"], allowance)
job = Job(label=label, kind="eval-set", eval_set_id="inv-" + label, config_path="x",
          submitted_at="now", estimated_usd=amount, reserved_usd=amount, status="pending")
try:
    r.reserve_and_record(job)
    print("accepted")
except Exception as ex:
    print("refused:", ex)
"""


def test_two_processes_cannot_both_take_the_last_of_the_allowance(tmp_path: Path) -> None:
    """Real contention, in two processes, because that is what the file lock is for.

    An earlier version of this test used asyncio.gather, which proved nothing: the
    reservation path has no await in it, so the two calls ran one after the other and
    the test passed with the lock deleted.
    """
    import subprocess
    import sys

    script = tmp_path / "reserve.py"
    script.write_text(RESERVE_SCRIPT.format(src=str(Path(__file__).parent.parent / "src")))
    (tmp_path / "work").mkdir(exist_ok=True)

    # each wants $2 of a $3 allowance; the first to take the lock holds it for 1s
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(tmp_path), label, "2", "3", "1"],
            stdout=subprocess.PIPE, text=True,
        )
        for label in ("first", "second")
    ]
    out = [p.communicate()[0].strip() for p in procs]
    assert sorted(o.split(":")[0] for o in out) == ["accepted", "refused"], out
    ledger = JobLedger(tmp_path)
    assert len(ledger.jobs) == 1 and ledger.reserved_usd() == 2.0


def test_each_job_gets_its_own_eval_set_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reused id makes Hawk resume that eval set instead of running a new job."""
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    _price("openrouter/openai/gpt-5.6-luna")
    r = remote(tmp_path, allowance=50.0)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    ids = []
    for name in ("inv-one", "inv-two"):
        config = _write(tmp_path, f"{name}.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name=name))
        run(hawk_submit(r, tmp_path)(config=config, estimated_usd=0.5, note=None))
        ids.append(JobLedger(tmp_path).get(name.removeprefix("inv-")).eval_set_id)  # type: ignore[union-attr]
    assert ids[0] != ids[1], "two jobs must not share an eval set id"
    assert all(i.startswith("inv-") for i in ids)
    # and each id is the one that was submitted and the one recorded as a log source
    assert set(ids) <= set(json.loads((tmp_path / "log_sources.json").read_text()))
    assert [yaml.safe_load(c.read_text())["eval_set_id"] for c in r.hawk.submitted] == ids  # type: ignore[attr-defined]


def test_a_lost_submission_response_is_reconciled_not_resubmitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hawk took the job but the answer never came back: the ledger must not lose it."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    landed: list[str] = []

    async def submit_then_lose_the_answer(config_path: Path) -> str:
        landed.append(str(yaml.safe_load(config_path.read_text())["eval_set_id"]))
        raise TimeoutError("connection reset while waiting for hawk")

    async def exists(eval_set_id: str) -> bool:
        return eval_set_id in landed

    _price("openrouter/openai/gpt-5.6-luna")
    r.hawk.submit = submit_then_lose_the_answer  # type: ignore[assignment]
    r.hawk.eval_set_exists = exists  # type: ignore[assignment]
    path = _write(tmp_path, "lost.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-lost"))
    with pytest.raises(ToolError, match="submission failed"):
        run(hawk_submit(r, tmp_path)(config=path, estimated_usd=1.0, note=None))

    job = JobLedger(tmp_path).get("lost")
    assert job is not None and job.eval_set_id == landed[0]
    assert job.status == "submitted", "Hawk has it; the job is not lost and must not be sent twice"
    assert job.reserved_usd == 1.0 and JobLedger(tmp_path).reserved_usd() == 1.0


def test_a_submission_that_never_reached_hawk_releases_its_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir(exist_ok=True)

    async def refuse(config_path: Path) -> str:
        raise RuntimeError("hawk eval-set run failed: could not resolve host")

    async def missing(eval_set_id: str) -> bool:
        return False

    _price("openrouter/openai/gpt-5.6-luna")
    r.hawk.submit = refuse  # type: ignore[assignment]
    r.hawk.eval_set_exists = missing  # type: ignore[assignment]
    path = _write(tmp_path, "gone.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-gone"))
    with pytest.raises(ToolError, match="never reached Hawk"):
        run(hawk_submit(r, tmp_path)(config=path, estimated_usd=1.0, note=None))
    ledger = JobLedger(tmp_path)
    assert ledger.get("gone").status == "failed"  # type: ignore[union-attr]
    assert ledger.reserved_usd() == 0.0


def test_collect_leaves_an_unpriced_cost_unknown_and_keeps_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    monkeypatch.setattr(_investigate, "usage_cost", lambda files: (None, {"openrouter/x": {"input": 1, "cache_read": 0, "output": 2}}, True))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "u.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-u")), estimated_usd=0.2, note=None))
    r.hawk.eval_status = "success"  # type: ignore[attr-defined]
    out = run(jobs(r, tmp_path)(action="collect", label="u", sample=None, wait_minutes=None, limit=None))
    assert "cost unknown" in out
    ledger = JobLedger(tmp_path)
    job = ledger.get("u")
    assert job is not None and job.actual_usd is None, "an estimate must never be recorded as a measurement"
    assert ledger.reserved_usd() == 1.0 and ledger.unpriced() == ["u"]


def test_submission_is_refused_when_a_named_model_has_no_registered_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unpriced model means the runner's cost limit cannot bind, so spend is unbounded.

    This is the bug the earlier version of this file hid: prices are registered under
    `openrouter/<id>` and a config names `<id>`, so looking one up by the other found
    nothing and stamped an empty cost table into the job.
    """
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    unpriced = "openai/gpt-5.6-nowhere"
    r = Remote(tmp_path, HAWK, None, TASK_PKG, AUDIT_PKG, IMAGE, [unpriced], 10.0)
    r.hawk = FakeHawk()  # type: ignore[assignment]
    (tmp_path / "work").mkdir(exist_ok=True)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    config = filled_example("benchmark.eval-set.yaml", name="inv-unpriced")
    config["models"][0]["items"][0]["name"] = unpriced
    with pytest.raises(ToolError, match=f"no registered price for openrouter/{unpriced}"):
        run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "unpriced.eval-set.yaml", config), estimated_usd=1.0, note=None))
    assert JobLedger(tmp_path).jobs == [], "a refused submission holds nothing"


def test_prices_cover_the_grader_role_as_well_as_the_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    r.known_sources.add("inv-staged-abc")
    _price("openrouter/openai/gpt-5.6-luna")
    _price("openrouter/openai/gpt-5-mini", input=0.25, output=2.0)
    config = filled_example("audit.eval-set.yaml", name="inv-priced")
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "priced.eval-set.yaml", config), estimated_usd=1.0, note=None))
    job = JobLedger(tmp_path).get("priced")
    assert job is not None
    stamped = yaml.safe_load(Path(job.config_path).read_text())["model_cost_config"]
    assert set(stamped) == {"openrouter/openai/gpt-5.6-luna", "openrouter/openai/gpt-5-mini"}


@pytest.mark.parametrize(
    "args, expect",
    [
        # a task that passes its arguments on to another constructor is the way past
        # every outer check, so the same rules apply at every depth
        ({"task_args": {"grader_model": "openrouter/evil/model"}}, "is not an allowed model"),
        ({"task_args": {"solver": "anything"}}, "task arg not allowed"),
        ({"task_args": {"benchmark_image": "ghcr.io/evil:v1"}}, "is not an allowed image"),
        ({"task_args": {"logs": "/var/run/secrets/kubernetes.io/serviceaccount/token"}}, "must be a hawk: source"),
        ({"task_args": {"nested": {"deeper": {"model": "openrouter/evil/model"}}}}, "is not an allowed model"),
        ({"task_args": [{"model": "openrouter/evil/model"}]}, "is not an allowed model"),
        ({"config": {"dataset": "s3://someone-elses/bucket"}}, "points outside this investigation"),
        ({"task_args": {"epochs": 9999}}, "must be a whole number from 1 to"),
    ],
)
def test_nested_task_arguments_face_the_same_rules(args: dict, expect: str) -> None:
    config = filled_example("benchmark.eval-set.yaml")
    config["tasks"][0]["items"][0]["args"] = {**config["tasks"][0]["items"][0]["args"], **args}
    problems = validate_config(config, policy(), set())
    assert any(expect in p for p in problems), problems


def test_a_size_is_only_what_the_eval_set_states() -> None:
    """A task argument called `limit` is the task's business and may not cap anything."""
    config = filled_example("benchmark.eval-set.yaml")
    del config["limit"]
    config["tasks"][0]["items"][0]["args"] = {"max_samples": 1}
    assert any("does not state its size" in p for p in validate_config(config, policy(), set()))


def test_repeated_work_is_reserved_and_the_knobs_that_multiply_it_are_capped() -> None:
    from inspect_audit._jobs import parse_config, worst_case_usd

    config = filled_example("benchmark.eval-set.yaml", limit=4, retry_attempts=2, cost_limit=0.5)
    parsed, problems = parse_config(config)
    assert problems == []
    # 0.50 x 4 samples x 1 model x 1 epoch x (1 original + 2 retries)
    assert worst_case_usd(parsed, policy()) == 6.0
    for key, value in (("retry_attempts", 100), ("max_connections", 5000), ("max_retries", 900)):
        assert any(
            key in p for p in validate_config(filled_example("benchmark.eval-set.yaml", **{key: value}), policy(), set())
        )


def test_a_refused_duplicate_does_not_overwrite_the_config_that_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The saved config is the evidence of what was submitted; it must stay true."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    _price("openrouter/openai/gpt-5.6-luna")
    r = remote(tmp_path, allowance=50.0)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    ran = _write(tmp_path, "one.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-dup", limit=2))
    run(hawk_submit(r, tmp_path)(config=ran, estimated_usd=0.5, note=None))
    saved = Path(JobLedger(tmp_path).get("dup").config_path)  # type: ignore[union-attr]
    before = saved.read_text()
    other = _write(tmp_path, "two.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-dup", limit=8))
    with pytest.raises(ToolError, match="already exists"):
        run(hawk_submit(r, tmp_path)(config=other, estimated_usd=0.5, note=None))
    assert saved.read_text() == before
    assert yaml.safe_load(saved.read_text())["limit"] == 2


def test_every_ledger_write_goes_through_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second process's reservation must survive this one's status updates."""
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    _price("openrouter/openai/gpt-5.6-luna")
    r = remote(tmp_path, allowance=50.0)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    run(hawk_submit(r, tmp_path)(config=_write(tmp_path, "a.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-mine", limit=2)), estimated_usd=0.5, note=None))

    # another process, sharing this investigation directory, reserves while we hold a
    # stale in-memory copy of the ledger
    elsewhere = JobLedger(tmp_path)
    with elsewhere.transaction() as ledger:
        ledger.add(
            Job(label="theirs", kind="eval-set", eval_set_id="inv-theirs-1", config_path="x",
                submitted_at="now", estimated_usd=1.0, reserved_usd=20.0, status="submitted")
        )

    run(jobs(r, tmp_path)(action="evals", label="mine", sample=None, wait_minutes=None, limit=None))
    run(jobs(r, tmp_path)(action="stop", label="mine", sample=None, wait_minutes=None, limit=None))
    after = JobLedger(tmp_path)
    assert {j.label for j in after.jobs} == {"mine", "theirs"}
    assert after.reserved_usd() == 21.0


@pytest.mark.parametrize(
    "args, expect",
    [
        # renaming the argument must not walk past the rule
        ({"model_name": "openrouter/evil/x"}, "is not an allowed model"),
        ({"judge": "openrouter/evil/x"}, "is not an allowed model"),
        ({"scorer_model_id": "openrouter/evil/x"}, "is not an allowed model"),
        ({"image_uri": "ghcr.io/evil:1"}, "is not an allowed image"),
        ({"docker_image_name": "ghcr.io/evil:1"}, "is not an allowed image"),
        ({"num_samples": 100000}, "must be a whole number"),
        ({"max_items": 100000}, "must be a whole number"),
        ({"opts": {"limit": 100000}}, "must be a whole number"),
        ({"dataset": "https://evil/data.jsonl"}, "points outside this investigation"),
        ({"dataset": "/etc/passwd"}, "points outside this investigation"),
        ({"dataset": "/home/someone/secrets"}, "points outside this investigation"),
        ({"dataset": "~/secrets"}, "points outside this investigation"),
    ],
)
def test_renaming_a_task_argument_does_not_get_past_the_rule(args: dict, expect: str) -> None:
    config = filled_example("benchmark.eval-set.yaml")
    config["tasks"][0]["items"][0]["args"] = {**config["tasks"][0]["items"][0]["args"], **args}
    problems = validate_config(config, policy(), set())
    assert any(expect in p for p in problems), problems


def test_ordinary_task_arguments_still_pass() -> None:
    """The rules must not refuse a task's real parameters, or nothing can be run."""
    config = filled_example("benchmark.eval-set.yaml")
    config["tasks"][0]["items"][0]["args"] = {
        "temperature": 0.0,
        "system_prompt": "Answer with the letter only.",
        "shuffle": True,
        "subset": "hard",
        "samples": ["q1", "q2"],
        "dataset_path": "/inputs/source/data.csv",
        "grader_model": "openai/gpt-5-mini",
        "auditor_image": IMAGE,
        "max_items": 3,
    }
    assert validate_config(config, policy(), set()) == []


def test_policy_rules_that_had_no_test(tmp_path: Path) -> None:
    """isolation, oversized sample_ids, and a limit range: each reachable, each refused."""
    item_isolation = filled_example("benchmark.eval-set.yaml")
    item_isolation["tasks"][0]["items"][0]["isolation"] = "standard"
    assert any("isolation" in p for p in validate_config(item_isolation, policy(), set()))

    many = filled_example("benchmark.eval-set.yaml")
    many["tasks"][0]["items"][0]["sample_ids"] = [str(i) for i in range(1001)]
    assert any("exceeds" in p for p in validate_config(many, policy(), set()))

    ranged = filled_example("benchmark.eval-set.yaml", limit=[0, 100])
    assert any("a range is not allowed" in p for p in validate_config(ranged, policy(), set()))

    empty = filled_example("benchmark.eval-set.yaml")
    empty["tasks"][0]["items"][0]["sample_ids"] = []
    assert validate_config(empty, policy(), set()) != []


def test_usage_cost_reads_real_logs_and_says_when_it_cannot_price_them(tmp_path: Path) -> None:
    """The function that decides priced from unpriced, run for real rather than stubbed."""
    from inspect_ai.model import ModelCost, ModelInfo, set_model_info
    from test_helpers.logs import run_fixture_eval

    from inspect_audit._jobs import usage_cost

    log = Path(run_fixture_eval(str(tmp_path / "logs")))
    cost, usage, recomputed = usage_cost([log])
    assert "mockllm/model" in usage and usage["mockllm/model"]["output"] > 0
    assert cost is None, "an unpriced model leaves the total unknown"
    assert recomputed, "no cost in the log means the number can only be recomputed"

    set_model_info(
        "mockllm/model",
        ModelInfo(cost=ModelCost(input=1000.0, output=1000.0, input_cache_read=0.0, input_cache_write=0.0)),
    )
    priced, _, recomputed = usage_cost([log])
    assert priced is not None and priced > 0
    assert recomputed, "the log recorded no cost, so this total is an estimate"


def test_eval_set_exists_parses_the_cli_and_does_not_match_a_different_id() -> None:
    """The oracle the whole recovery path rests on, exercised against real CLI output."""
    from inspect_audit._jobs import Hawk

    h = Hawk("https://hawk.example", None)
    table = (
        "Eval Sets\n"
        "ID                          Created              Creator\n"
        "inv-smoke-1234abcd          2026-09-09 18:00     james\n"
    )
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: int = 600) -> str:
        calls.append(args)
        return table

    h._run = fake_run  # type: ignore[method-assign]
    assert run(h.eval_set_exists("inv-smoke-1234abcd")) is True
    assert calls[0][:3] == ("list", "eval-sets", "--search")
    assert run(h.eval_set_exists("inv-other-9999zzzz")) is False


def test_the_fake_hawk_matches_the_real_one() -> None:
    """Tests are only worth their fake: every method must exist with the same signature."""
    import inspect as inspect_module

    from inspect_audit._jobs import Hawk

    real = {
        name: inspect_module.signature(getattr(Hawk, name))
        for name in dir(Hawk)
        if not name.startswith("_") and callable(getattr(Hawk, name))
    }
    fake = FakeHawk()
    for name, signature in real.items():
        assert hasattr(fake, name), f"FakeHawk is missing {name}(); tests using it prove nothing"
        theirs = inspect_module.signature(getattr(fake, name))
        assert list(theirs.parameters) == [p for p in signature.parameters if p != "self"], (
            f"FakeHawk.{name}{theirs} does not match Hawk.{name}{signature}"
        )
        assert inspect_module.iscoroutinefunction(getattr(fake, name)), (
            f"Hawk.{name} is async; a synchronous fake would hide a blocking call"
        )


def test_submit_passes_the_secrets_file_and_the_flags_the_run_needs() -> None:
    """Without --secrets-file the runner starts with no provider key and every sample fails."""
    from inspect_audit._jobs import Hawk

    seen: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: int = 600) -> str:
        seen.append(args)
        return "Eval set ID: inv-abc-123\n"

    h = Hawk("https://hawk.example", "/path/to/.env")
    h._run = fake_run  # type: ignore[method-assign]
    assert run(h.submit(Path("/tmp/c.yaml"))) == "inv-abc-123"
    args = seen[0]
    assert args[:2] == ("eval-set", "run") and "/tmp/c.yaml" in args
    assert "--skip-confirm" in args and "--log-dir-allow-dirty" in args
    assert args[args.index("--secrets-file") + 1] == "/path/to/.env"

    without = Hawk("https://hawk.example", None)
    without._run = fake_run  # type: ignore[method-assign]
    run(without.submit(Path("/tmp/c.yaml")))
    assert "--secrets-file" not in seen[1]


def test_hawk_runs_through_inspects_subprocess_with_the_api_url_it_was_given() -> None:
    """Inspect's subprocess keeps the event loop free and counts against max_subprocesses."""
    from inspect_audit import _jobs

    captured: dict[str, object] = {}

    class Result:
        success = True
        stdout = "ok"
        stderr = ""

    async def fake_subprocess(args, text=True, env=None, timeout=None, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["args"], captured["env"], captured["timeout"] = args, env, timeout
        return Result()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(_jobs, "subprocess", fake_subprocess)
    try:
        run(_jobs.Hawk("https://hawk.example", None).logs("inv-x"))
    finally:
        monkey.undo()
    env = captured["env"]
    assert isinstance(env, dict) and env["HAWK_API_URL"] == "https://hawk.example"
    assert captured["args"][:2] == ["hawk", "logs"]  # type: ignore[index]
    assert captured["timeout"] == 120


def test_a_cost_the_runner_recorded_is_used_as_measured(tmp_path: Path) -> None:
    """The runner prices its own usage; recomputing here would re-price it at today's rates."""
    from inspect_ai.log import read_eval_log, write_eval_log
    from inspect_ai.model import ModelUsage
    from test_helpers.logs import run_fixture_eval

    from inspect_audit._jobs import usage_cost

    path = Path(run_fixture_eval(str(tmp_path / "logs")))
    log = read_eval_log(str(path))
    log.stats.model_usage = {
        "openrouter/openai/gpt-5.6-luna": ModelUsage(
            input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000, total_cost=1.40
        )
    }
    write_eval_log(log, str(path))

    cost, usage, recomputed = usage_cost([path])
    assert cost == 1.40 and not recomputed
    assert usage["openrouter/openai/gpt-5.6-luna"]["input"] == 1_000_000


def test_logs_already_parked_where_hawk_can_read_them_are_not_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warehouse address is a reference, not a payload: nothing is downloaded or staged.

    A benchmark's logs can be tens of gigabytes. Copying them onto a laptop to run an
    investigation, and then uploading them again so a job can read them, is work nobody
    wants twice.
    """
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "t.py").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "t.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.org", "commit", "-qm", "c"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/org/bench.git"], check=True)

    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
    target = _investigate.investigate(
        str(repo),
        logs=["hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs"],
        output_dir=str(tmp_path / "runs"),
        enforce_cost_limit=False,
        hawk_api_url=HAWK,
        secrets_file=str(tmp_path / ".env"),
    )
    root = Path(target.metadata["investigation_dir"])
    seed = json.loads((root / "inputs/seed.json").read_text())
    assert seed["logs"] == [
        {
            "source": "hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs",
            "staged": None,
            "remote": "hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs",
        }
    ]
    assert not (root / "inputs" / "logs").exists(), "nothing was copied"
    assert not (root / "staged.json").exists()
    assert seed["remote"]["supplied_logs"] == ["hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs"]

    # and a job may read exactly that address, not its neighbours
    sources = set(json.loads((root / "log_sources.json").read_text()))
    assert "hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs" in sources
    config = filled_example("audit.eval-set.yaml", name="inv-parked")
    config["tasks"][0]["items"][0]["args"]["logs"] = "hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs"
    assert validate_config(config, policy(), sources) == []
    config["tasks"][0]["items"][0]["args"]["logs"] = "hawk:audit-epoch-chess-p2/inputs/something-else"
    assert any("staged or ran" in p for p in validate_config(config, policy(), sources))


def test_reading_a_parked_log_source_stays_inside_the_investigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent may read the sources it was given, by name, and nothing else."""
    from inspect_ai.tool import ToolError

    from inspect_audit._investigate import supplied_logs

    (tmp_path / "work").mkdir()
    (tmp_path / "inputs").mkdir()
    r = remote(tmp_path)
    address = "hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs"
    tool = supplied_logs(r, tmp_path, [address])

    from inspect_audit._investigate import _alias

    alias = _alias(address)
    listing = run(tool(action="list", source=None, sample=None, limit=None))
    assert alias in listing and address in listing

    # a source it was not given, however plausible
    with pytest.raises(ToolError, match="unknown log source"):
        run(tool(action="samples", source="audit-chess-terra-review", sample=None, limit=None))
    with pytest.raises(ToolError, match="unknown log source"):
        run(tool(action="samples", source="../../etc", sample=None, limit=None))

    out = run(tool(action="samples", source=alias, sample=None, limit=5))
    assert "samples.csv" in out
    written = tmp_path / "inputs" / "index" / alias / "samples.csv"
    assert written.is_file()
    header = written.read_text().splitlines()[0]
    assert header.startswith("uuid,id,epoch,model,task_name,status")

    # a transcript from another eval set is refused even though the operator's
    # credentials could fetch it
    with pytest.raises(ToolError, match="not in"):
        run(tool(action="transcript", source=alias, sample="someone-elses-uuid", limit=None))
    mine = f"{address.removeprefix('hawk:').split('/')[0]}-s1"
    assert "transcripts/" in run(tool(action="transcript", source=alias, sample=mine, limit=None))
    assert (tmp_path / "inputs" / "index" / alias / "transcripts" / f"{mine}.md").is_file()

    with pytest.raises(ToolError, match="action must be"):
        run(tool(action="delete", source=alias, sample=None, limit=None))


def test_a_local_only_investigation_has_no_log_reading_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files under /inputs/logs are read with bash; a tool for them would be noise."""
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)
    log = tmp_path / "a.eval"
    log.write_bytes(b"x")
    target = _investigate.investigate(
        str(git_repo(tmp_path / "repo")),
        logs=[str(log)],
        output_dir=str(tmp_path / "runs"),
        enforce_cost_limit=False,
    )
    names = {t.__name__ if hasattr(t, "__name__") else "" for t in target.solver.__dict__.get("tools", [])}
    assert "logs" not in names


def test_remote_work_refuses_to_start_without_a_secrets_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every job in the first real run failed with 401 because none was found.

    The default looked only where Inspect looks, upwards from the working directory,
    and the run was launched from a worktree with no .env in it. The agent then spent
    twenty minutes discovering that its runners could not authenticate.
    """
    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "register_openrouter_costs", lambda: 0)
    monkeypatch.setattr(_investigate, "find_dotenv", lambda usecwd=True: "")
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "t.py").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "t.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.org", "commit", "-qm", "c"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/org/bench.git"], check=True)

    with pytest.raises(ValueError, match="needs a secrets file"):
        _investigate.investigate(
            str(repo), output_dir=str(tmp_path / "runs"), hawk_api_url=HAWK, enforce_cost_limit=False
        )

    # beside the benchmark is one of the places it looks
    (repo / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
    target = _investigate.investigate(
        str(repo), output_dir=str(tmp_path / "runs2"), hawk_api_url=HAWK, enforce_cost_limit=False
    )
    assert target.metadata["investigation_dir"]

    # and beside the investigation file, which wins
    (repo / ".env").unlink()
    config = tmp_path / "here" / "investigation.yaml"
    config.parent.mkdir()
    config.write_text(f"repo: {repo}\noutput_dir: {tmp_path / 'runs3'}\nhawk_api_url: {HAWK}\nenforce_cost_limit: false\n")
    (config.parent / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
    assert _investigate.investigate(config=str(config)).metadata["investigation_dir"]


def test_a_name_is_free_again_when_its_submission_never_reached_hawk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first run lost a job name to a failed submission and had to invent v2."""
    from inspect_ai.tool import ToolError

    from inspect_audit import _investigate

    monkeypatch.setattr(_investigate, "_local_spend", lambda: (0.0, []))
    _price("openrouter/openai/gpt-5.6-luna")
    r = remote(tmp_path, allowance=50.0)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    path = _write(tmp_path, "j.eval-set.yaml", filled_example("benchmark.eval-set.yaml", name="inv-once"))

    async def refuse(config_path: Path) -> str:
        raise RuntimeError("hawk eval-set run failed: Missing secrets")

    async def missing(eval_set_id: str) -> bool:
        return False

    r.hawk.submit = refuse  # type: ignore[assignment]
    r.hawk.eval_set_exists = missing  # type: ignore[assignment]
    with pytest.raises(ToolError, match="never reached Hawk"):
        run(hawk_submit(r, tmp_path)(config=path, estimated_usd=1.0, note=None))
    assert JobLedger(tmp_path).get("once").status == "failed"  # type: ignore[union-attr]

    # the operator fixes the secrets and the agent submits the same job again
    r.hawk = FakeHawk()  # type: ignore[assignment]
    out = run(hawk_submit(r, tmp_path)(config=path, estimated_usd=1.0, note=None))
    assert "Submitted" in out
    ledger = JobLedger(tmp_path)
    assert len([j for j in ledger.jobs if j.label == "once"]) == 1
    assert ledger.get("once").status == "submitted"  # type: ignore[union-attr]


def test_two_log_sources_cannot_share_a_name(tmp_path: Path) -> None:
    """Truncated aliases collided and the second source vanished from the mapping."""
    from inspect_audit._investigate import _alias, supplied_logs

    a = "hawk:simpleqa-verified-sweep-2026-05-luna-abcdefgh/inputs/logs"
    b = "hawk:simpleqa-verified-sweep-2026-05-terra-ijklmnop/inputs/logs"
    assert _alias(a) != _alias(b)
    assert len(_alias(a)) <= 31

    (tmp_path / "work").mkdir()
    tool = supplied_logs(None, tmp_path, [a, b])
    listing = run(tool(action="list", source=None, sample=None, limit=None))
    assert a in listing and b in listing
    assert _alias(a) in listing and _alias(b) in listing


def test_reading_a_subdirectory_says_it_covers_the_whole_eval_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The warehouse indexes by eval set, so a narrower address is not a narrower read."""
    from inspect_audit._investigate import _alias, supplied_logs

    (tmp_path / "work").mkdir()
    (tmp_path / "inputs").mkdir()
    r = remote(tmp_path)
    address = "hawk:audit-epoch-chess-p2/inputs/epoch-chess-logs"
    out = run(
        supplied_logs(r, tmp_path, [address])(
            action="samples", source=_alias(address), sample=None, limit=None
        )
    )
    assert "covers the whole set" in out and "epoch-chess-logs" in out


def test_a_mode_name_is_not_a_model(tmp_path: Path) -> None:
    """`scorer: original` is a control the live run wanted and the policy refused.

    A model reference names its provider. A bare word cannot reach a paid provider from
    a runner holding one key, so refusing it bought nothing and cost the investigation
    its comparison against the benchmark's other scorer.
    """
    for args in ({"scorer": "original"}, {"grader": "default"}, {"judge": "strict"}):
        config = filled_example("benchmark.eval-set.yaml")
        config["tasks"][0]["items"][0]["args"] = {**config["tasks"][0]["items"][0]["args"], **args}
        assert validate_config(config, policy(), set()) == [], args

    # a provider-qualified name is still checked
    config = filled_example("benchmark.eval-set.yaml")
    config["tasks"][0]["items"][0]["args"] = {"grader_model": "openrouter/anthropic/claude-opus-5"}
    assert any("not an allowed model" in p for p in validate_config(config, policy(), set()))


def test_sample_pagination_keeps_page_size_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _jobs.Hawk(HAWK, None)
    calls = []

    async def page(eval_set_id, number, size):
        calls.append((number, size))
        return [{"id": i} for i in range((number - 1) * size, number * size)]

    monkeypatch.setattr(h, "_samples_page", page)
    rows = run(h.samples("set", 300))
    assert [r["id"] for r in rows] == list(range(300))
    assert calls == [(1, 250), (2, 250)]


def test_api_token_refreshes_once_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error
    import urllib.request

    h = _jobs.Hawk(HAWK, None)
    h._token = "expired"
    tokens = []

    async def fresh(*args, **kwargs):
        return "fresh"

    def response(request, timeout):
        tokens.append(request.get_header("Authorization"))
        if len(tokens) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, "Expired", {}, None)
        return io.BytesIO(b'{"items": [{"uuid": "s"}]}')

    monkeypatch.setattr(h, "_run", fresh)
    monkeypatch.setattr(urllib.request, "urlopen", response)
    assert run(h._samples_page("set", 1, 250)) == [{"uuid": "s"}]
    assert tokens == ["Bearer expired", "Bearer fresh"]


def test_generated_configs_pass_actual_policy_and_hawk_schema(tmp_path: Path) -> None:
    from inspect_audit._investigate import write_experiment_templates

    r = remote(tmp_path)
    write_experiment_templates(r, tmp_path, "inspect_evals/simpleqa_verified")
    for f in (tmp_path / "work/jobs/templates").glob("*.yaml"):
        config = yaml.safe_load(f.read_text())
        assert validate_config(config, r.policy, r.known_sources) == []
        assert config["working_limit"] < config["time_limit"]
    assert len(list((tmp_path / "work/jobs/templates").glob("*.yaml"))) == 2


def test_remote_evidence_is_checked_before_investigation(tmp_path: Path) -> None:
    from inspect_audit._investigate import check_evidence_access

    r = remote(tmp_path)
    (tmp_path / "inputs").mkdir()
    seed = tmp_path / "inputs/seed.json"
    seed.write_text('{}')
    run(check_evidence_access(r, tmp_path, ["hawk:valid", "s3://unsupported"])(None, None))
    checks = json.loads(seed.read_text())["evidence_access"]
    assert [c["status"] for c in checks] == ["readable", "unavailable"]
    assert list((tmp_path / "inputs/index").rglob("*.md"))


@pytest.mark.parametrize('change', [
    {'runner': 'wrong'}, {'runner': {'secrets': ['OPENROUTER_API_KEY']}},
    {'models': ['openai/gpt-5.6-luna']}, {'model_roles': [{}]},
    {'runner': {'environment': ['A=B']}},
])
def test_malformed_config_is_refused_not_crashed(change: dict) -> None:
    config = filled_example('benchmark.eval-set.yaml')
    config.update(change)
    assert validate_config(config, policy(), set())


@pytest.mark.parametrize('args', [
    {'grader_config': {'model': 'anthropic/unknown', 'api_key': 'fake'}},
    {'judge': [{'name': 'x', 'base_url': 'https://outside.example'}]},
    {'n_samples': 1e9}, {'epochs': 99.0}, {'dataset_path': '/inputs/../../etc/passwd'},
])
def test_nested_policy_regressions(args: dict) -> None:
    config = filled_example('benchmark.eval-set.yaml')
    config['tasks'][0]['items'][0]['args'].update(args)
    assert validate_config(config, policy(), set())


def test_role_reservation_is_per_evaluated_model() -> None:
    import copy
    config = filled_example('benchmark.eval-set.yaml')
    parsed, errors = _jobs.parse_config(config)
    assert not errors
    baseline = _jobs.worst_case_usd(parsed, policy())
    config['model_roles'] = {'grader': copy.deepcopy(config['models'][0])}
    config['models'].append(copy.deepcopy(config['models'][0]))
    parsed, errors = _jobs.parse_config(config)
    assert not errors
    assert _jobs.worst_case_usd(parsed, policy()) == baseline * 4


def test_eval_pagination_sees_unfinished_tail(monkeypatch) -> None:
    h = _jobs.Hawk('https://hawk.example', None)
    row = {'task_name': 't', 'model': 'm', 'status': 'success', 'completed_samples': 1, 'total_samples': 1}
    pages = [[dict(row, id=str(i)) for i in range(h.PAGE)], [dict(row, status='running')]]
    monkeypatch.setattr(h, '_metadata_page', lambda *args: _returns(pages.pop(0)))
    result = run(h.evals('set'))
    assert len(result) == h.PAGE + 1
    assert result[-1]['status'] == 'running'


def test_repeated_sample_page_is_not_an_infinite_population(monkeypatch) -> None:
    h = _jobs.Hawk('https://hawk.example', None)
    monkeypatch.setattr(h, '_samples_page', lambda *args: _returns([{'uuid': str(i)} for i in range(h.PAGE)]))
    with pytest.raises(RuntimeError, match='repeated'):
        run(h.samples('set'))


def test_failed_ledger_transaction_restores_memory(tmp_path: Path) -> None:
    ledger = JobLedger(tmp_path)
    with pytest.raises(ValueError):
        with ledger.transaction():
            ledger.add(Job('a', 'audit', 'id', 'x', 'now', 1))
            raise ValueError('abort')
    assert ledger.jobs == []
    assert JobLedger(tmp_path).jobs == []


def test_explicit_judge_arguments_accept_qualified_openrouter_names() -> None:
    config = filled_example('benchmark.eval-set.yaml')
    args = config['tasks'][0]['items'][0]['args']
    args.update(refusal_judge='openrouter/' + policy().models[-1],
                semantic_judge='openrouter/' + policy().models[-1])
    assert validate_config(config, policy(), set()) == []
    args['semantic_judge'] = 'openrouter/anthropic/not-allowed'
    assert any('not an allowed model' in p for p in validate_config(config, policy(), set()))
