"""Remote work for the investigator: Hawk eval-set jobs, a ledger, and reservations.

The investigator's shell lives in a container with no credentials. Everything here runs
in the trusted Inspect process on the host, using the operator's Hawk login (the `hawk`
CLI and its keyring). Job
state is a JSON ledger in the investigation directory so a restarted session sees what
was already submitted instead of launching it again.
"""

import fcntl
import json
import os
import posixpath
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path
from typing import Any

import anyio
import yaml
from inspect_ai.util import display_counter, subprocess

logger = getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
TERMINAL = {"success", "error", "cancelled"}


@dataclass
class Job:
    label: str
    kind: str  # benchmark | audit
    eval_set_id: str
    config_path: str
    submitted_at: str
    estimated_usd: float  # what the agent expected; kept to compare against reality
    reserved_usd: float = 0.0  # what the config can cost at worst; held until collected
    # pending: written before the CLI call, so a lost response is recoverable.
    # failed: the submission never reached Hawk; its reservation is released.
    status: str = "pending"
    actual_usd: float | None = None
    collected_to: str | None = None
    note: str = ""
    evals: list[dict[str, Any]] = field(default_factory=list)


class JobLedger:
    """Every remote job this investigation launched, on disk, under a file lock.

    Reservations are checked and written inside the same locked transaction, so two
    submissions running at once cannot both fit into the last of the allowance.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / "jobs.json"
        self.lock_path = root / "jobs.lock"
        self.jobs: list[Job] = []
        self.reload()

    def reload(self) -> None:
        self.jobs = (
            [Job(**j) for j in json.loads(self.path.read_text())]
            if self.path.is_file()
            else []
        )

    @contextmanager
    def transaction(self) -> Iterator["JobLedger"]:
        """Exclusive access: re-read from disk, yield, write back on a clean exit."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                self.reload()
                yield self
                self.save()
            except BaseException:
                self.reload()
                raise
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def save(self) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps([asdict(j) for j in self.jobs], indent=2))
        os.replace(temporary, self.path)

    def get(self, label: str) -> Job | None:
        return next((j for j in self.jobs if j.label == label), None)

    def add(self, job: Job) -> None:
        if self.get(job.label) is not None:
            raise ValueError(f"a job labelled {job.label!r} already exists")
        self.jobs.append(job)

    def reserved_usd(self) -> float:
        """Money held against jobs that are alive and whose real cost is not yet known."""
        return sum(
            j.reserved_usd
            for j in self.jobs
            if j.actual_usd is None and j.status != "failed"
        )

    def actual_usd(self) -> float:
        return sum(j.actual_usd or 0.0 for j in self.jobs)

    def unpriced(self) -> list[str]:
        """Collected jobs whose cost could not be computed; their reservation stands."""
        return [
            j.label
            for j in self.jobs
            if j.actual_usd is None and j.collected_to is not None
        ]


class Hawk:
    """Thin wrapper over the `hawk` CLI, which holds the operator's login."""

    def __init__(self, api_url: str, secrets_file: str | None, binary: str = "hawk") -> None:
        self.env = {"HAWK_API_URL": api_url}
        self.secrets_file = secrets_file
        self.binary = binary
        self._token = ""

    async def _run(self, *args: str, timeout: int = 600) -> str:
        """One `hawk` invocation, through Inspect's subprocess rather than blocking.

        `inspect_ai.util.subprocess` keeps the event loop free, counts against the
        eval's `max_subprocesses` limit, and terminates a hung child properly. A
        blocking `subprocess.run` here would stall every other sample in the eval for
        as long as Hawk takes to answer.
        """
        result = await subprocess(
            [self.binary, *args], text=True, env=self.env, timeout=timeout
        )
        if not result.success:
            raise RuntimeError(
                f"hawk {' '.join(args[:2])} failed: {(result.stderr or result.stdout)[-1500:]}"
            )
        return str(result.stdout)

    async def submit(self, config_path: Path) -> str:
        args = ["eval-set", "run", str(config_path), "--skip-confirm", "--log-dir-allow-dirty"]
        if self.secrets_file:
            args += ["--secrets-file", self.secrets_file]
        out = await self._run(*args)
        match = re.search(r"Eval set ID:\s*(\S+)", out)
        if not match:
            raise RuntimeError(f"could not find the eval set id in hawk's output:\n{out[-800:]}")
        return match.group(1)

    async def eval_set_exists(self, eval_set_id: str) -> bool:
        """Whether Hawk has this eval set, used to resolve a submission with no answer."""
        out = await self._run("list", "eval-sets", "--search", eval_set_id, "--limit", "50", timeout=120)
        return eval_set_id in out

    async def evals(self, eval_set_id: str) -> list[dict[str, str]]:
        """Read every eval through Hawk's paginated metadata endpoint."""
        rows: list[dict[str, str]] = []
        page = 1
        seen: set[str] = set()
        while True:
            batch = await self._metadata_page("evals", eval_set_id, page, self.PAGE)
            signature = json.dumps(batch, sort_keys=True)
            if batch and signature in seen:
                raise RuntimeError("Hawk repeated an eval page; completion is unknown")
            seen.add(signature)
            rows.extend({"task": str(r["task_name"]), "model": str(r["model"]),
                         "status": str(r["status"]),
                         "samples": f"{r['completed_samples']}/{r['total_samples']}"}
                        for r in batch)
            if len(batch) < self.PAGE:
                return rows
            page += 1

    # Keep a stable page size (Hawk accepts up to 500).
    PAGE = 250

    async def samples(self, eval_set_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Every sample in the set, or the first `limit`, a page at a time.

        A run of a thousand items over seven models is seven thousand samples; a single
        page of it is not a population, and a request for all of it is refused.
        """
        collected: list[dict[str, Any]] = []
        page = 1
        seen: set[str] = set()
        while True:
            want = self.PAGE if limit is None else min(self.PAGE, limit - len(collected))
            if want <= 0:
                break
            rows = await self._samples_page(eval_set_id, page, self.PAGE)
            signature = json.dumps(rows, sort_keys=True)
            if rows and signature in seen:
                raise RuntimeError("Hawk repeated a sample page; population coverage is unknown")
            seen.add(signature)
            collected += rows[:want]
            if len(rows) < self.PAGE:
                break
            page += 1
        return collected

    async def _samples_page(self, eval_set_id: str, page: int, limit: int) -> list[dict[str, Any]]:
        return await self._metadata_page("samples", eval_set_id, page, limit)

    async def has_sample(self, eval_set_id: str, sample_uuid: str) -> bool:
        rows = await self._metadata_page("samples", eval_set_id, 1, self.PAGE, search=sample_uuid)
        return any(str(row.get("uuid")) == sample_uuid for row in rows)

    async def _metadata_page(self, resource: str, eval_set_id: str, page: int, limit: int, *, search: str | None = None) -> list[dict[str, Any]]:
        import urllib.error
        import urllib.parse
        import urllib.request

        query = urllib.parse.urlencode(
            {"eval_set_id": eval_set_id, "page": page, "limit": limit}
            | ({"search": search} if search else {})
        )
        for attempt in range(2):
            token = await self.access_token()
            request = urllib.request.Request(
                f"{self.env['HAWK_API_URL'].rstrip('/')}/meta/{resource}?{query}",
                headers={"Authorization": f"Bearer {token}"},
            )

            def fetch(request: urllib.request.Request = request) -> list[dict[str, Any]]:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return list(json.load(response).get("items", []))

            try:
                return await anyio.to_thread.run_sync(fetch)
            except urllib.error.HTTPError as ex:
                if ex.code != 401 or attempt:
                    raise
                self._token = ""
        raise AssertionError("unreachable")

    async def access_token(self) -> str:
        """The operator's Hawk token, from the CLI that holds their login."""
        if not self._token:
            self._token = (await self._run("auth", "access-token", timeout=60)).strip()
        return self._token

    async def download(self, eval_set_id: str, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        await self._run("download", eval_set_id, "--output-dir", str(out_dir), timeout=1800)
        return sorted(out_dir.rglob("*.eval"))

    async def stop(self, eval_set_id: str) -> None:
        await self._run("stop", eval_set_id, timeout=300)

    async def logs(self, eval_set_id: str, lines: int = 120) -> str:
        """Tail of the runner's own log, the place install failures and crashes show up."""
        out = await self._run("logs", eval_set_id, "-n", str(lines), timeout=120)
        return out[-6000:]

    async def watch(self, eval_set_id: str) -> str:
        """One-shot live status: per-task and per-sample phase, retries, limits, trouble."""
        out = await self._run("watch", eval_set_id, "--no-follow", timeout=180)
        return out[-8000:]

    async def status(self, eval_set_id: str) -> str:
        """The raw monitoring report: pod status, metrics, recent logs, as JSON."""
        out = await self._run("status", eval_set_id, timeout=300)
        return out[-8000:]

    async def trace(self, eval_set_id: str, lines: int = 100) -> str:
        """Runner's in-flight actions. An `enter` with no `exit` is what is hanging now."""
        out = await self._run("trace", eval_set_id, "-n", str(lines), timeout=180)
        return out[-8000:]

    async def stacktrace(self, eval_set_id: str) -> str:
        """py-spy dump of the live runner's thread stacks; running pod only."""
        out = await self._run("stacktrace", eval_set_id, timeout=300)
        return out[-8000:]

    async def transcript(self, sample_uuid: str, out_dir: Path) -> Path:
        """One sample's transcript as markdown, written to a file rather than returned."""
        out_dir.mkdir(parents=True, exist_ok=True)
        text = await self._run("transcript", sample_uuid, timeout=600)
        path = out_dir / f"{sample_uuid}.md"
        path.write_text(text)
        return path

    async def transcripts(self, eval_set_id: str, out_dir: Path, limit: int | None = None) -> list[Path]:
        """Every sample's transcript in the set, written to out_dir."""
        out_dir.mkdir(parents=True, exist_ok=True)
        args = ["transcripts", eval_set_id, "--output-dir", str(out_dir)]
        if limit is not None:
            args += ["--limit", str(limit)]
        await self._run(*args, timeout=1800)
        return sorted(p for p in out_dir.iterdir() if p.is_file())


@dataclass
class Policy:
    """What a submitted eval-set config may contain. Enforced in code, not prompt.

    A Hawk eval-set config runs arbitrary Python from `packages:` inside a runner that
    holds the operator's provider key and S3 credentials, so the config is treated as
    hostile input: allowlists for everything that names code or credentials, hard caps
    on spend-shaped fields, and no unknown keys at any level.
    """

    packages: list[str]  # exact git/pip specs allowed in packages: and tasks[].package
    task_names: list[str]  # registry package names allowed in tasks[].name
    models: list[str]  # OpenRouter model ids allowed anywhere a model is named
    auditor_images: list[str]  # every image any task argument may name
    hawk_api_url: str
    secrets: tuple[str, ...] = ("OPENROUTER_API_KEY",)
    env_keys: tuple[str, ...] = ("HAWK_API_URL", "HAWK_RUNNER_REFRESH_URL")
    max_limit: int = 1000
    max_epochs: int = 5
    max_token_limit: int = 10_000_000
    max_time_limit: int = 14_400
    # dollars per sample, enforced by the runner through Inspect's cost limit. The
    # reservation a job holds is this multiplied by the samples the config asks for.
    max_cost_limit_usd: float = 5.0
    max_worst_case_usd: float = 200.0
    # knobs that multiply the work or the request rate. Retries repeat a failed task,
    # so they multiply the reservation as well as being capped here.
    max_retry_attempts: int = 3
    max_message_limit: int = 10_000
    max_connections: int = 50
    max_retries: int = 20
    id_prefix: str = "inv-"


ALLOWED_TOP_LEVEL = {
    "name", "eval_set_id", "packages", "tasks", "models", "model_roles", "runner", "limit",
    "sample_shuffle", "epochs", "token_limit", "time_limit", "message_limit", "working_limit",
    "cost_limit", "max_connections", "max_retries", "retry_attempts", "timeout", "metadata",
    "tags", "log_images", "log_model_api", "score",
}
ALLOWED_RUNNER = {"environment", "secrets", "memory"}
ALLOWED_MODEL_ARGS = {"base_url", "config"}
ALLOWED_MODEL_CONFIG = {"reasoning_effort", "max_tokens", "temperature", "reasoning_tokens"}
# task argument names that decide what runs, what it costs, or what it can reach.
# Anything matching is checked against the policy; everything else is the task's own
# parameter, executed by code that is already allowlisted.
# Substring, not suffix: `model_name`, `judge`, `image_uri` and `num_samples` all decide
# what runs or what it costs, and an allowlist keyed on exact spellings is walked around
# by renaming the argument. Anything that mentions one of these concepts is checked.
MODEL_ARG = re.compile(r"model|judge|grader|scorer|extractor|llm")
IMAGE_ARG = re.compile(r"image|container|registry")
SIZE_ARG = re.compile(r"^(n|count|limit|epochs)$|limit|sample|epoch|item|batch|repeat|attempt")
LOG_ARG = re.compile(r"^logs?$|log_dir|log_file|transcript")
FORBIDDEN_ARGS = {
    "setup", "sandbox", "sandboxes", "solver", "agent", "approval", "secrets", "secret",
    "env", "environment", "command", "entrypoint", "token", "key", "api_key",
    "credentials", "aws_profile", "bucket",
}


def parse_config(config: dict[str, Any]) -> tuple[Any, list[str]]:
    """Parse with Hawk's own schema, so the effective settings are what we check.

    Returns the parsed EvalSetConfig, or the schema's complaints. Hawk allows extra
    top-level keys, so this does not replace the allowlists; it resolves the shape.
    """
    try:
        from hawk.core.types.evals import EvalSetConfig
    except ImportError as ex:  # pragma: no cover - install-time problem, not a code path
        raise RuntimeError(
            "remote work needs Hawk's config schema: pip install 'inspect_audit[remote]'"
        ) from ex
    import pydantic

    try:
        return EvalSetConfig.model_validate(config), []
    except pydantic.ValidationError as ex:
        return None, [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in ex.errors()[:12]
        ]


def _model_items(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for group in config.get("models") or []:
        found.append((f"models[{group.get('name')}]", group))
    for role, group in (config.get("model_roles") or {}).items():
        found.append((f"model_roles.{role}", group))
    return found


def _task_arg_problems(
    where: str, args: dict[str, Any], policy: Policy, known_log_sources: set[str]
) -> list[str]:
    """The task arguments are the configuration that actually executes.

    Checked at every depth. Tasks pass nested mappings through to other constructors
    (inspect_audit's own `audit` task hands `task_args` straight to the audited task),
    so a rule applied only to the outer keys is a rule an inner key walks around: a
    grader model, an image or a size nested one level down would otherwise reach the
    runner unexamined.
    """
    problems: list[str] = []

    def walk(prefix: str, value: Any, depth: int = 0) -> None:
        if depth > 8:  # a config this deep is not a task argument
            problems.append(f"{where}: {prefix} is nested too deeply to check")
            return
        if isinstance(value, dict):
            for key, inner in value.items():
                check(f"{prefix}.{key}" if prefix else str(key), str(key), inner, depth)
            return
        if isinstance(value, list):
            for index, inner in enumerate(value):
                walk(f"{prefix}[{index}]", inner, depth + 1)
            return
        if isinstance(value, str):
            _string_problems(where, prefix, value, problems)

    def check(path: str, key: str, value: Any, depth: int) -> None:
        lowered = key.lower()
        if lowered in FORBIDDEN_ARGS:
            problems.append(f"{where}: task arg not allowed: {path!r}")
            return
        if MODEL_ARG.search(lowered):
            for model in value if isinstance(value, list) else [value]:
                if not isinstance(model, str):
                    walk(path, model, depth + 1)
                    continue
                # a model reference names its provider; a bare word is a mode, not a
                # model, and cannot reach a paid provider from a runner that holds one
                # key. `scorer: original` is a real control and was being refused.
                allowed = set(policy.models) | {
                    name if name.startswith("openrouter/") else f"openrouter/{name}"
                    for name in policy.models
                }
                if "/" in model and model not in allowed:
                    problems.append(f"{where}: {path}={model!r} is not an allowed model")
                elif "/" not in model and model in policy.models:
                    continue
            return
        if IMAGE_ARG.search(lowered):
            if value is not None and value not in policy.auditor_images:
                problems.append(f"{where}: {path}={value!r} is not an allowed image")
            return
        if SIZE_ARG.search(lowered):
            if not isinstance(value, (int, float)) or value is True or value is False:
                # a name-shaped size argument holding something else (a list of ids, a
                # path, a flag) is checked as whatever it is, not as a number
                walk(path, value, depth + 1)
                return
            cap = policy.max_epochs if "epoch" in lowered else policy.max_limit
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or not (0 < value <= cap)
            ):
                problems.append(f"{where}: {path}={value!r} must be a whole number from 1 to {cap}")
            return
        if LOG_ARG.search(lowered):
            for source in value if isinstance(value, list) else [value]:
                if not _known_log_source(str(source), known_log_sources):
                    problems.append(
                        f"{where}: {path}={source!r} must be a hawk: source this "
                        "investigation staged or ran"
                    )
            return
        walk(path, value, depth + 1)

    walk("", args)
    return problems


# a task argument may name data, a prompt or a flag; it may not name a location outside
# the job, because the runner holds credentials that reach some of those locations
_OUTSIDE = re.compile(r"^(s3://|gs://|hawk:|file://|https?://|ftp://|//)")
_ALLOWED_ABSOLUTE = ("/inputs/", "/workspace/", "/tmp/")


def _string_problems(where: str, path: str, text: str, problems: list[str]) -> None:
    if (
        _OUTSIDE.match(text)
        or text.startswith("~")
        or (text.startswith("/") and not posixpath.normpath(text).startswith(_ALLOWED_ABSOLUTE))
    ):
        problems.append(
            f"{where}: {path} points outside this investigation: {text[:60]!r}"
        )


def _known_log_source(logs: str, known_log_sources: set[str]) -> bool:
    """Either an eval set this investigation created, or an address it was handed.

    The operator may park logs somewhere a runner can read and name that address in
    the investigation, in which case the address itself is the authorisation and is
    matched exactly; nothing near it is implied.
    """
    if logs in known_log_sources:
        return True
    return (
        logs.startswith("hawk:")
        and logs.removeprefix("hawk:").split("/")[0] in known_log_sources
    )


def _samples_in(item: Any, config_limit: int | None) -> int | None:
    """How many samples one task item runs, or None when it does not say.

    Only `sample_ids` and the eval set's own `limit` are trusted. A task argument
    called `limit` is the task's business and may mean something else entirely, so
    believing it would let a job that runs a whole dataset reserve the price of one
    sample.
    """
    if item.sample_ids:
        return len(item.sample_ids)
    return config_limit


def worst_case_usd(parsed: Any, policy: Policy) -> float | None:
    """What a submitted config is expected to cost at its own stated ceiling.

    Hawk runs every task item against every model, `epochs` times, and re-runs a
    failed task up to `retry_attempts` times; the runner stops a sample once Inspect
    sees it pass `cost_limit` dollars. It is a stopping threshold rather than a hard
    ceiling, so the sample in flight can overshoot it, and a model the task builds for
    itself out of an unpriced name is not counted by it at all. Treat this as the
    number to hold against the allowance, not as a guarantee.

    None means the config does not bound itself, which is a refusal, not an unknown.
    """
    cost_limit = parsed.cost_limit
    if not isinstance(cost_limit, (int, float)) or cost_limit <= 0:
        return None
    limit = parsed.limit if isinstance(parsed.limit, int) and parsed.limit > 0 else None
    epochs = parsed.epochs
    epochs = epochs if isinstance(epochs, int) else getattr(epochs, "epochs", 1) or 1
    if not isinstance(epochs, int) or epochs < 1:
        return None
    models = sum(len(group.items) for group in parsed.models or []) or 1
    # Scoring is outside Inspect's solver cost limit. Reserve an additional
    # allowance per declared role per evaluated model; this is a planning
    # buffer, not an enforced ceiling on arbitrary scorer code.
    models *= 1 + len(parsed.model_roles or {})
    attempts = 1 + max(0, parsed.retry_attempts or 0)
    samples = 0
    for task in parsed.tasks:
        for item in task.items:
            per_item = _samples_in(item, limit)
            if per_item is None or per_item < 1:
                return None
            samples += per_item
    return float(cost_limit) * samples * models * int(epochs) * attempts


def validate_config(
    config: dict[str, Any], policy: Policy, known_log_sources: set[str]
) -> list[str]:
    """Every way the config could do something other than run an allowed eval, as a list.

    Empty list means submit. `known_log_sources` are the `hawk:` sources this
    investigation created (staged inputs, its own finished jobs).
    """
    problems: list[str] = []
    if not isinstance(config, dict):
        return ["config must be a mapping"]
    parsed, schema_problems = parse_config(config)
    if schema_problems:
        return [f"Hawk rejects this config: {p}" for p in schema_problems]
    unknown = set(config) - ALLOWED_TOP_LEVEL
    if {"generate_config", "max_tokens"} & unknown:
        problems.append("Put generation settings under models[].items[].args.config (and the corresponding model_roles item), e.g. args.config.max_tokens.")
    if "max_samples" in unknown:
        problems.append("max_samples is controlled by Hawk infrastructure, not this job. "
                        "Use smaller limit batches, working_limit for active work, and "
                        "a generous time_limit for queued wall time.")
    if unknown:
        problems.append(f"keys not allowed: {sorted(unknown)}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", str(config.get("name", ""))):
        problems.append("name must be lowercase letters, digits, hyphens (2-41 chars)")
    if "eval_set_id" in config:
        problems.append(
            "remove eval_set_id: a fresh one is assigned at submission. Reusing an id makes "
            "Hawk resume that eval set rather than run a new job, and `logs` reaches a "
            "separately imported source through the Hawk API whatever this job's id is"
        )
    for pkg in config.get("packages") or []:
        if pkg not in policy.packages:
            problems.append(f"package not allowed: {pkg!r}")
    if not config.get("tasks"):
        problems.append("tasks is required")
    if not config.get("models"):
        problems.append("models is required")

    # the raw-dict checks run before the parse, so a config that Hawk's schema also
    # dislikes still hears the policy's objection rather than only the schema's
    runner = config.get("runner") or {}
    if set(runner) - ALLOWED_RUNNER:
        problems.append(f"runner keys not allowed: {sorted(set(runner) - ALLOWED_RUNNER)} (no image, cpu, cleanup)")
    env = runner.get("environment") or {}
    if set(env) - set(policy.env_keys):
        problems.append(f"runner.environment keys not allowed: {sorted(set(env) - set(policy.env_keys))}")
    if env.get("HAWK_RUNNER_REFRESH_URL", None) != "" or env.get("HAWK_API_URL") != policy.hawk_api_url:
        problems.append(
            f"runner.environment must set HAWK_API_URL to {policy.hawk_api_url} and HAWK_RUNNER_REFRESH_URL to ''"
        )
    for secret in runner.get("secrets") or []:
        if secret.get("name") not in policy.secrets or set(secret) - {"name", "description", "type"} or secret.get("type", "env") != "env":
            problems.append(f"runner secret not allowed: {secret}")
    if config.get("secrets"):
        problems.append("top-level secrets are not allowed; runner.secrets holds the provider key")
    for where, group in _model_items(config):
        if group.get("package") != "openai" or group.get("name") != "openrouter":
            problems.append(f"{where}: models must use package openai, provider openrouter")
        for item in group.get("items") or []:
            if item.get("name") not in policy.models:
                problems.append(f"{where}: model not allowed: {item.get('name')!r}")
            args = item.get("args") or {}
            if args.get("base_url") != OPENROUTER_BASE_URL:
                problems.append(f"{where}: args.base_url must be {OPENROUTER_BASE_URL}")
            extra = set(args) - ALLOWED_MODEL_ARGS
            if extra:
                problems.append(f"{where}: model args not allowed: {sorted(extra)}")
            cfg_extra = set(args.get("config") or {}) - ALLOWED_MODEL_CONFIG
            if cfg_extra:
                problems.append(f"{where}: model config keys not allowed: {sorted(cfg_extra)}")

    parsed, schema_problems = parse_config(config)
    if schema_problems:
        return problems + [f"Hawk rejects this config: {p}" for p in schema_problems]

    for task in parsed.tasks:
        if task.package not in policy.packages:
            problems.append(f"task package not allowed: {task.package!r}")
        if task.name not in policy.task_names:
            problems.append(f"task registry package not allowed: {task.name!r}")
        for item in task.items:
            where = f"tasks[{task.name}].{item.name}"
            if item.secrets:
                problems.append(f"{where}: task-level secrets are not allowed")
            if item.isolation is not None:
                problems.append(f"{where}: isolation is the operator's to set")
            if item.sample_ids and len(item.sample_ids) > policy.max_limit:
                problems.append(f"{where}: {len(item.sample_ids)} sample_ids exceeds {policy.max_limit}")
            problems += _task_arg_problems(where, item.args or {}, policy, known_log_sources)

    epochs = parsed.epochs if isinstance(parsed.epochs, int) else getattr(parsed.epochs, "epochs", None)
    for key, value, cap in (
        ("epochs", epochs, policy.max_epochs),
        ("token_limit", parsed.token_limit, policy.max_token_limit),
        ("time_limit", parsed.time_limit, policy.max_time_limit),
    ):
        if value is None:
            problems.append(f"{key} is required by the investigation policy")
        elif not isinstance(value, int) or value > cap:
            problems.append(f"{key} {value!r} must be an integer up to {cap}")
    if parsed.limit is not None and (
        not isinstance(parsed.limit, int) or not (0 < parsed.limit <= policy.max_limit)
    ):
        problems.append(
            f"limit {parsed.limit!r} must be a whole number from 1 to {policy.max_limit}; "
            "a range is not allowed"
        )
    for task in parsed.tasks:
        for item in task.items:
            if item.sample_ids is not None and not item.sample_ids:
                problems.append(f"tasks[{task.name}].{item.name}: sample_ids is empty")
    for key, cap in (
        ("retry_attempts", policy.max_retry_attempts),
        ("message_limit", policy.max_message_limit),
        ("working_limit", policy.max_time_limit),
        ("max_connections", policy.max_connections),
        ("max_retries", policy.max_retries),
        ("timeout", policy.max_time_limit),
    ):
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value <= cap):
            problems.append(f"{key} {value!r} must be a whole number up to {cap}")
    if parsed.cost_limit is None:
        problems.append(
            f"cost_limit is required: dollars per sample, up to {policy.max_cost_limit_usd}. "
            "It limits solver spending and determines the reservation; scoring and in-flight calls can exceed it"
        )
    elif not (0 < parsed.cost_limit <= policy.max_cost_limit_usd):
        problems.append(
            f"cost_limit {parsed.cost_limit} must be above 0 and at most {policy.max_cost_limit_usd}"
        )

    worst = worst_case_usd(parsed, policy)
    if worst is None and not problems:
        problems.append(
            "the job does not state its size: set limit, or sample_ids on every task item"
        )
    elif worst is not None and worst > policy.max_worst_case_usd:
        problems.append(
            f"reserved allowance ${worst:,.2f} (cost_limit x samples x models x epochs, with role/retry buffers) exceeds "
            f"the ${policy.max_worst_case_usd:,.2f} a single job may hold; run it in parts"
        )
    return problems

def task_package_name(spec: str) -> str:
    """The registry name of a package from its git or pip spec.

    git+https://.../inspect_evals@sha -> inspect_evals; inspect-evals==1.0 -> inspect_evals.
    """
    tail = spec.split("#")[0].rstrip("/").split("/")[-1]
    tail = tail.split("@")[0].removesuffix(".git")
    tail = re.split(r"[=<>!~ ]", tail)[0]
    return tail.replace("-", "_")


def write_config(root: Path, label: str, config: dict[str, Any]) -> Path:
    path = root / "jobs" / f"{label}.eval-set.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def slug(text: str) -> str:
    """A Hawk-safe eval set id fragment: lowercase alphanumerics and hyphens."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", text.lower())).strip("-")[:30]


def usage_cost(logs: list[Path]) -> tuple[float | None, dict[str, dict[str, int]], bool]:
    """What a finished job cost, from the logs it wrote.

    The runner prices its own usage: every submitted config carries the prices, so
    Inspect records `total_cost` per model in the log's stats. That number is the
    measurement, and it is what the job was actually charged at the time it ran.

    A log written before prices were supplied has no `total_cost`; then this recomputes
    from the prices registered here, at today's rates, and says so through the third
    return value, because a recomputation is an estimate and the ledger has to know the
    difference. If a model has no price at all the total is None.
    """
    from inspect_ai.log import read_eval_log
    from inspect_ai.model import get_model_info

    total = 0.0
    priced = True
    recomputed = False
    usage: dict[str, dict[str, int]] = {}
    for path in logs:
        header = read_eval_log(str(path), header_only=True)
        for model, u in (header.stats.model_usage or {}).items():
            usage.setdefault(model, {"input": 0, "cache_read": 0, "output": 0})
            usage[model]["input"] += u.input_tokens or 0
            usage[model]["cache_read"] += u.input_tokens_cache_read or 0
            usage[model]["output"] += u.output_tokens or 0
            if u.total_cost is not None:
                total += u.total_cost
                continue
            recomputed = True
            info = get_model_info(model)
            cost = info.cost if info else None
            if cost is None:
                priced = False
                continue
            total += (
                (u.input_tokens or 0) * (cost.input or 0)
                + (u.input_tokens_cache_read or 0) * (cost.input_cache_read or 0)
                + (u.input_tokens_cache_write or 0) * (cost.input_cache_write or 0)
                + (u.output_tokens or 0) * (cost.output or 0)
            ) / 1_000_000
    return (total if priced else None), usage, recomputed


async def wait_for(
    hawk: Hawk, eval_set_id: str, minutes: float, poll_seconds: float = 60
) -> list[dict[str, str]]:
    """Poll until every eval in the set is terminal or the wait expires. No model calls.

    `anyio.sleep` rather than `time.sleep`: this waits for minutes at a time, and
    holding a thread for that long stalls whatever else the eval is doing.
    """
    deadline = time.monotonic() + minutes * 60
    rows: list[dict[str, str]] = []
    while True:
        rows = await hawk.evals(eval_set_id)
        if rows and all(r["status"] in TERMINAL for r in rows):
            return rows
        if time.monotonic() >= deadline:
            return rows
        display_counter("hawk", f"waiting on {eval_set_id}")
        await anyio.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))


def copy_into_inputs(files: list[Path], inputs: Path, label: str) -> Path:
    """Place collected logs under inputs/jobs/<label>/ so the container sees them read-only."""
    dest = inputs / "jobs" / label
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = dest / f.name
        if target.exists():
            target.unlink()
        try:
            target.hardlink_to(f)
        except OSError as ex:
            import errno

            if ex.errno != errno.EXDEV:
                raise
            shutil.copyfile(f, target)
    return dest


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
