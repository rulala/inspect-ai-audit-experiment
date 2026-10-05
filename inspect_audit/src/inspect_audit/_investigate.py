"""Local, artifact-first investigation using Inspect's standard agent and ACP."""

import errno
import inspect as inspect_module
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from html.parser import HTMLParser
from importlib.metadata import version
from logging import getLogger
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from dotenv import find_dotenv
from inspect_ai import Task, task
from inspect_ai.agent import AgentState, react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs, transcript
from inspect_ai.model import (
    CompactionSummary,
    GenerateConfig,
    ModelCost,
    ModelInfo,
    get_model_info,
    set_model_info,
)
from inspect_ai.model._model import sample_model_usage
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, ToolError, bash, skill, tool
from inspect_ai.util import LimitExceededError, sample_limits, sandbox, store_as

from . import prompts
from ._agent import SKILLS, SUPPORT_SKILLS, view_image
from ._jobs import (
    Hawk,
    Job,
    JobLedger,
    Policy,
    copy_into_inputs,
    parse_config,
    slug,
    task_package_name,
    usage_cost,
    utcnow,
    validate_config,
    wait_for,
    worst_case_usd,
    write_config,
)
from ._report import (
    InvestigationState,
    _operator_turn,
)
from ._report import (
    publish_report as publish_report,
)
from ._report import (
    save_publication as save_publication,
)
from ._report import (
    validate_findings as validate_findings,
)

logger = getLogger(__name__)

ASSETS = Path(__file__).parent / "investigation"

# Ours first, then the vendored ones, adapted for a container with no user in it and no
# `hawk` binary. See investigation/skills/VENDORED.md for provenance and what changed.
DEFAULT_AUDITOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "072e50b2ea1c51e67644e97e08cff052a52a1d661294635e1c3e360d1371b9ee"
)

INVESTIGATION_SKILLS = (
    "investigating",
    "writing",
    "running-jobs",
    "eval-validity-review",
    "investigate-dataset",
    "security-audit-eval",
    "view-results",
    "debug-stuck-eval",
)
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
DEFAULT_WORKERS = [
    "openai/gpt-5.6-luna",
    "google/gemini-3.6-flash",
    "openai/gpt-5-mini",
    "openai/gpt-5.6-sol",
    "openai/gpt-5.6-terra",
    "anthropic/claude-sonnet-5",
    "openai/gpt-6-astra",
]


def _snapshot_repo(
    repo: str, revision: str | None, inputs: Path, paths: list[str] | None
) -> dict[str, object]:
    source = Path(repo).expanduser()
    if source.is_dir():
        commit = subprocess.check_output(
            [
                "git",
                "-C",
                str(source),
                "rev-parse",
                "--verify",
                f"{revision or 'HEAD'}^{{commit}}",
            ],
            text=True,
        ).strip()
        # only the named paths when given: the task under audit, not the whole
        # collection it ships in -- other evals are noise the agent should not read
        archive = inputs / "source.tar"
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                f"--output={archive}",
                commit,
                *(["--", *paths] if paths else []),
            ],
            check=True,
        )
        # unpacked here rather than in the box: the agent should find files, not a tar
        tree = inputs / "source"
        tree.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(tree, filter="data")
        archive.unlink()
        return {
            "repo": str(source.resolve()),
            "revision": commit,
            "paths": paths or ["."],
            "snapshot": "/inputs/source",
            "source": "committed snapshot; excludes working-tree changes and submodule contents",
        }
    if not repo.startswith("https://"):
        raise ValueError("repo must be a local Git repository or an HTTPS Git URL")
    return {
        "repo": repo,
        "revision": revision or "HEAD",
        "paths": paths or ["."],
        "snapshot": None,
        "source": "remote repository; clone it yourself into /workspace",
    }


def _fetch_paper(paper: str, inputs: Path) -> str:
    """Stage the paper under inputs/paper/: a local file, or a URL downloaded now.

    An arXiv abstract URL is turned into its PDF. A failed download keeps the URL
    in the seed so the agent can try itself, with the failure recorded.
    """
    local = Path(paper).expanduser()
    (inputs / "paper").mkdir(exist_ok=True)
    if local.is_file():
        shutil.copyfile(local.resolve(), inputs / "paper" / local.name)
        return f"/inputs/paper/{local.name}"
    if not paper.startswith(("http://", "https://")):
        return paper
    url = re.sub(r"arxiv\.org/abs/([^v?#]+)(v\d+)?", r"arxiv.org/pdf/\1\2", paper)
    name = Path(url.split("?", 1)[0].rstrip("/")).name or "paper"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "inspect_audit"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except Exception as ex:  # network is optional at prep time
        logger.warning(f"could not download the paper from {url}: {ex}")
        return paper
    (inputs / "paper" / name).write_bytes(data)
    return f"/inputs/paper/{name}"


def _stage_docs(docs: list[str], inputs: Path) -> list[str]:
    """Copy documentation trees read-only into inputs/docs/<name>/."""
    staged: list[str] = []
    for source in docs:
        path = Path(source).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Docs directory does not exist: {source}")
        dest = inputs / "docs" / path.name
        shutil.copytree(
            path,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "node_modules"),
        )
        staged.append(f"/inputs/docs/{path.name}")
    return staged


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError as ex:
        if ex.errno != errno.EXDEV:
            raise
        shutil.copyfile(src, dest)


def register_openrouter_costs(timeout: float = 15) -> int:
    """Register OpenRouter's current prices with Inspect, so a cost limit can bind.

    Inspect refuses a cost-limited run for a model without a price. OpenRouter
    publishes per-token prices for everything it serves; register them all as
    `openrouter/<id>` at prep time. Best effort: no network, no prices, and the
    cost limit then fails loudly at start rather than silently not applying.
    """
    try:
        request = urllib.request.Request(
            OPENROUTER_MODELS, headers={"User-Agent": "inspect_audit"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            models = json.load(response)["data"]
    except Exception as ex:
        logger.warning(f"could not fetch OpenRouter prices: {ex}")
        return 0
    registered = 0
    for model in models:
        pricing = model.get("pricing") or {}
        try:
            per_million = {
                key: float(pricing.get(field) or 0) * 1_000_000
                for key, field in (
                    ("input", "prompt"),
                    ("output", "completion"),
                    ("input_cache_read", "input_cache_read"),
                    ("input_cache_write", "input_cache_write"),
                )
            }
        except (TypeError, ValueError):
            continue
        try:
            # set_model_info creates the entry; set_model_cost needs one to exist
            set_model_info(
                f"openrouter/{model['id']}",
                (get_model_info(f"openrouter/{model['id']}") or ModelInfo()).model_copy(
                    update={"cost": ModelCost(**per_million)}
                )
            )
        except Exception as ex:  # one odd listing must not lose the rest
            logger.debug(f"skipping price for {model.get('id')}: {ex}")
            continue
        registered += 1
    return registered


def _find_secrets(config: str | None, repo: Path) -> str | None:
    """The .env a Hawk runner should be given, looked for where one is kept.

    Beside the investigation file, beside the benchmark, then Inspect's own search from
    the working directory. The working directory alone is not enough: a run launched
    from a checkout of this package finds nothing, and the failure lands in every
    runner as a 401 rather than here.
    """
    for directory in [Path(config).expanduser().parent if config else None, repo, repo.parent]:
        if directory and (directory / ".env").is_file():
            return str(directory / ".env")
    return find_dotenv(usecwd=True) or None


def _investigation_file(config: str, passed: dict[str, Any]) -> dict[str, Any]:
    """Task settings from a YAML file, so an investigation is a document, not a command.

    Every key is a parameter of this task, and an argument given on the command line
    wins over the file, so a saved investigation can be re-run with one thing changed.
    """
    path = Path(config).expanduser()
    if not path.is_file():
        raise ValueError(f"no such investigation file: {config}")
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{config} must be a mapping of task settings")
    signature = inspect_module.signature(investigate)
    unknown = set(loaded) - set(signature.parameters) - {"config"}
    if unknown:
        raise ValueError(
            f"{config} sets things this task does not take: {sorted(unknown)}. "
            f"Available: {sorted(k for k in signature.parameters if k != 'config')}"
        )
    # a value given on the command line beats the file. Every overridable parameter
    # defaults to None, so "given" is "not None": passing the same value the default
    # would have used is still passing it, and a file cannot turn something back on
    return {
        key: value
        for key, value in loaded.items()
        if key != "config" and passed.get(key) is None
    }


def _only_task(source: Path) -> str | None:
    """The task, when the repository declares exactly one and nobody said which."""
    declared: set[str] = set()
    for path in sorted(source.rglob("*.py")):
        if any(part in {".git", "tests", "test", "build", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r"@task(?:\([^)]*\))?\s*\ndef\s+(\w+)", text):
            named = re.search(r'name\s*=\s*["\']([^"\']+)["\']', match.group(0))
            declared.add(named.group(1) if named else match.group(1))
    if len(declared) == 1:
        only = declared.pop()
        logger.info(f"auditing {only}: the only task {source} declares")
        return only
    return None


def is_remote_logs(source: str) -> bool:
    """Whether a log source is already parked somewhere a Hawk job can read it."""
    return source.startswith(("hawk:", "s3://", "gs://", "http://", "https://"))


def git_package_spec(repo: Path, revision: str | None = None) -> str | None:
    """The pip spec that installs this checkout's code somewhere else.

    A Hawk runner is a fresh pod: it installs the benchmark to run it. That spec is
    the same repository the agent reads, at the same commit, so deriving it here
    removes the chance of the agent auditing one commit while the jobs run another.
    """
    try:
        origin = subprocess.check_output(
            ["git", "-C", str(repo), "remote", "get-url", "origin"], text=True
        ).strip()
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{revision or 'HEAD'}^{{commit}}"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not origin.startswith(("https://", "git@", "ssh://")):
        return None
    url = origin.removesuffix(".git").replace("git@github.com:", "https://github.com/")
    on_remote = subprocess.run(
        ["git", "-C", str(repo), "branch", "-r", "--contains", commit],
        capture_output=True, text=True,
    )
    if on_remote.returncode != 0 or not on_remote.stdout.strip():
        logger.warning(
            f"{url}@{commit[:8]} is not on any remote branch: a Hawk runner will not be "
            "able to install it. Push the commit, or pass the package spec explicitly."
        )
    return f"git+{url}@{commit}"


def own_package_spec() -> str | None:
    """The spec that installs the inspect_audit a runner should use: this one."""
    from importlib.metadata import Distribution, PackageNotFoundError

    try:
        dist = Distribution.from_name("inspect_audit")
    except PackageNotFoundError:  # pragma: no cover - not installed
        return None
    direct_url = dist.read_text("direct_url.json")
    if direct_url:
        data = json.loads(direct_url)
        vcs = data.get("vcs_info") or {}
        if vcs.get("vcs") == "git" and vcs.get("commit_id"):
            return f"git+{data['url'].removesuffix('.git')}@{vcs['commit_id']}"
        # an editable or local install: the code is a working tree, so ask git
        local = data.get("url", "")
        if local.startswith("file://"):
            return git_package_spec(Path(local.removeprefix("file://")))
    return None


def paths_from_metadata(source: Path, target_task: str | None) -> list[str] | None:
    """The task's own directory, plus what the package shares with it.

    Auditing one eval out of a collection, the rest of the collection is noise: the
    agent reads a hundred other tasks' code looking for the one it was asked about.
    The eval's own metadata says which directory is its own; the shared modules next
    to it are included because the task imports them.
    """
    directory = _task_directory(source, target_task)
    if directory is None:
        return None
    chosen = {str(directory.relative_to(source))}
    chosen |= {
        str(path.relative_to(source)) for path in _imported_by(source, directory)
    }
    for top in ("pyproject.toml", "README.md"):
        if (source / top).is_file():
            chosen.add(top)
    # a file inside a directory already named adds nothing to the snapshot
    directories = {c for c in chosen if (source / c).is_dir()}
    return sorted(
        c for c in chosen if not any(c.startswith(f"{d}/") for d in directories)
    )


def _imported_by(source: Path, directory: Path, depth: int = 3) -> set[Path]:
    """Everything in the package that the task's own code imports, transitively.

    Naming the task's directory and a fixed list of likely shared filenames is a guess,
    and the guess is wrong the moment a scorer lives in an ordinary sibling module. This
    follows the imports instead: a module the code actually names is in the snapshot, and
    a module it does not name is not.
    """
    package = _package_root(directory)
    if package is None:
        return set()
    found: set[Path] = set()
    frontier = [directory]
    pattern = re.compile(rf"(?:from|import)\s+({re.escape(package.name)}(?:\.\w+)*)")
    while frontier and depth > 0:
        depth -= 1
        imported: set[Path] = set()
        for start in frontier:
            files = sorted(start.rglob("*.py")) if start.is_dir() else [start]
            for module in files:
                try:
                    text = module.read_text(errors="ignore")
                except OSError:
                    continue
                for dotted in pattern.findall(text):
                    target = _module_path(package, dotted)
                    if target is not None and target not in found and directory not in target.parents:
                        imported.add(target)
        found |= imported
        # a module's own imports count too: the scorer that imports the constants
        frontier = list(imported)
    # the package's own __init__ is how the registry finds any of it
    if (package / "__init__.py").is_file():
        found.add(package / "__init__.py")
    return found


def _package_root(directory: Path) -> Path | None:
    """The importable package a directory belongs to: the outermost one with __init__."""
    root = None
    for candidate in [directory, *directory.parents]:
        if (candidate / "__init__.py").is_file():
            root = candidate
        elif root is not None:
            break
    return root


def _module_path(package: Path, dotted: str) -> Path | None:
    """Where `package.a.b` lives on disk, as a directory or a module file."""
    parts = dotted.split(".")[1:]
    if not parts:
        return None
    path = package.joinpath(*parts)
    if path.is_dir():
        return path
    module = path.with_suffix(".py")
    return module if module.is_file() else None


def _task_directory(source: Path, target_task: str | None) -> Path | None:
    """Where an eval's own code lives.

    Two ways, because benchmarks are not all shaped like `inspect_evals`. Its evals
    publish an `eval.yaml` naming their tasks, and that is the most reliable answer
    where it exists. Everything else is found the way a person would: the file that
    declares the task with `@task`. A benchmark that hides its task behind a factory
    defeats both, and then the whole repository is snapshotted, which is the old
    behaviour rather than a wrong answer.
    """
    if not target_task:
        return None
    name = target_task.split("/")[-1]
    for path in sorted(source.rglob("eval.yaml")):
        tasks = _safe_yaml(path).get("tasks")
        names = {
            str(entry.get("name", ""))
            for entry in (tasks if isinstance(tasks, list) else [])
            if isinstance(entry, dict)
        }
        if name in names or path.parent.name == name:
            return path.parent
    return _declaring_file(source, name)


# `@task` may name the task itself, or take the function's name. Both spellings, and
# the registry name may carry spaces where the function has underscores.
def _declaring_file(source: Path, name: str) -> Path | None:
    function = re.compile(rf"^\s*def {re.escape(name.replace(' ', '_').lower())}\s*\(", re.M)
    declared = re.compile(rf"@task\([^)]*name\s*=\s*[\"']{re.escape(name)}[\"']", re.S)
    fallback: Path | None = None
    for path in sorted(source.rglob("*.py")):
        if any(part in {".git", "tests", "test", "build", ".venv"} for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "@task" not in text:
            continue
        if declared.search(text):
            return path.parent
        if function.search(text) and fallback is None:
            fallback = path.parent
    return fallback


def _safe_yaml(path: Path) -> dict[str, object]:
    try:
        loaded = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def paper_from_metadata(source: Path, target_task: str | None) -> str | None:
    """The paper an eval names in its own metadata, when it names one."""
    if not target_task:
        return None
    directory = _task_directory(source, target_task)
    if directory is not None:
        arxiv = str(_safe_yaml(directory / "eval.yaml").get("arxiv") or "").strip()
        if arxiv:
            # several papers means the eval was revised; the last is the current one
            return arxiv.split(",")[-1].strip()
    return None


def prepare_workspace(
    repo: str,
    revision: str | None,
    paths: list[str] | None,
    logs: list[str],
    paper: str | None,
    docs: list[str],
    overview: str,
    target_task: str | None,
    output_dir: str,
    budget_usd: float,
) -> Path:
    """Create a fresh run directory; expose only explicit inputs to the shell."""
    root = Path(output_dir).expanduser().resolve() / uuid4().hex
    inputs = root / "inputs"
    work = root / "work"
    inputs.mkdir(parents=True)
    work.mkdir()
    seed: dict[str, object] = dict(_snapshot_repo(repo, revision, inputs, paths))
    log_index = []
    for index, source in enumerate(logs):
        if is_remote_logs(source):
            # already parked where a job can read it: nothing to copy, and the agent
            # reads it by asking Hawk rather than off the filesystem
            log_index.append({"source": source, "staged": None, "remote": source})
            continue
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Local log source does not exist: {source}")
        files = (
            [path]
            if path.is_file()
            else [
                Path(info.name.removeprefix("file://")).resolve()
                for info in list_eval_logs(str(path), recursive=True)
            ]
        )
        if not files:
            raise ValueError(f"No Inspect logs found: {source}")
        for file in files:
            relative = Path(str(index)) / (
                file.name if path.is_file() else file.relative_to(path)
            )
            _link_or_copy(file, inputs / "logs" / relative)
            log_index.append(
                {"source": str(file), "staged": f"/inputs/logs/{relative}", "remote": None}
            )
    staged_paper = _fetch_paper(paper, inputs) if paper else None
    staged_docs = _stage_docs(docs, inputs)
    seed.update(
        task=target_task,
        overview=overview,
        paper=staged_paper,
        docs=staged_docs,
        logs=log_index,
        budget_usd=budget_usd,
    )
    (inputs / "seed.json").write_text(json.dumps(seed, indent=2))
    shutil.copytree(
        ASSETS / "report",
        work / "report",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (work / "journal.md").write_text(
        "# Activity journal\n\nAppend actions and corrections with evidence references.\n"
    )
    (root / "Dockerfile").write_text(
        (ASSETS / "Dockerfile")
        .read_text()
        .replace("INSPECT_VERSION", version("inspect-ai"))
    )
    (root / ".dockerignore").write_text("*\n!Dockerfile\n")
    compose = {
        "services": {
            "default": {
                "build": {"context": "."},
                "command": "sleep infinity",
                "init": True,
                "working_dir": "/workspace",
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(inputs),
                        "target": "/inputs",
                        "read_only": True,
                    },
                    {"type": "bind", "source": str(work), "target": "/workspace"},
                ],
            }
        }
    }
    (root / "compose.yaml").write_text(yaml.safe_dump(compose))
    return root


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.images: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "img":
            src = dict(attrs).get("src") or ""
            self.images.append(src[:60] + ("…" if len(src) > 60 else ""))
        if tag in ("p", "h1", "h2", "h3", "h4", "li", "tr", "pre", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


@tool
def render_report() -> Tool:
    """Render the draft and read it back, without publishing."""

    async def execute() -> str:
        """Render report/report.qmd to HTML and return its text and any warnings.

        Use this to check the draft renders and reads the way you intend before
        calling publish_report. Figures are listed by source; look at a figure
        with view_image. Nothing is saved outside the workspace.
        """
        result = await sandbox().exec(
            ["quarto", "render", "/workspace/report/report.qmd", "--to", "html"],
            timeout=300,
        )
        if not result.success:
            raise ToolError(f"Rendering failed:\n{result.stderr}\n{result.stdout}")
        html = await sandbox().read_file("/workspace/report/report.html")
        parser = _Text()
        parser.feed(html)
        text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
        warnings = "\n".join(
            line for line in (result.stderr or "").splitlines() if "WARN" in line
        )
        # the template embeds resources, so every figure in the HTML is a data: URI
        # and its src says nothing. The files behind them are what view_image reads.
        listing = await sandbox().exec(
            [
                "find", "/workspace/report/evidence", "-maxdepth", "2", "-type", "f",
                "-name", "*.png", "-o", "-name", "*.jpg", "-o", "-name", "*.jpeg",
                "-o", "-name", "*.svg", "-o", "-name", "*.webp",
            ],
            timeout=60,
        )
        files = sorted(line for line in (listing.stdout or "").splitlines() if line.strip())[:40]
        return (
            f"Rendered ({len(text)} characters of text, {len(parser.images)} figures).\n"
            + (f"Quarto warnings:\n{warnings}\n" if warnings else "")
            + (
                "Figure files to look at with view_image:\n  " + "\n  ".join(files) + "\n"
                if files
                else "No figure files under /workspace/report/evidence/.\n"
            )
            + f"\n{text[:6000]}"
            + ("\n…" if len(text) > 6000 else "")
        )

    return execute


class Remote:
    """Everything the dispatch tools need that the agent must not hold."""

    def __init__(
        self,
        root: Path,
        hawk_api_url: str,
        secrets_file: str | None,
        task_package: str,
        audit_package: str,
        auditor_image: str,
        worker_models: list[str],
        allowance_usd: float,
    ) -> None:
        self.root = root
        self.hawk = Hawk(hawk_api_url, secrets_file)
        self.hawk_api_url = hawk_api_url
        self.task_package = task_package
        self.audit_package = audit_package
        self.auditor_image = auditor_image
        self.worker_models = worker_models
        self.allowance_usd = allowance_usd
        self.ledger = JobLedger(root)
        self.policy = Policy(
            packages=[task_package, audit_package],
            task_names=[task_package_name(task_package), "inspect_audit"],
            models=worker_models,
            auditor_images=[auditor_image],
            hawk_api_url=hawk_api_url,
        )
        self._spend_path = root / "local_spend.json"
        spent = (
            json.loads(self._spend_path.read_text()) if self._spend_path.is_file() else {}
        )
        # a resumed run starts Inspect's usage accounting from zero; what earlier runs
        # of this investigation spent is carried forward from disk
        self.prior_local_usd = float(spent.get("prior_usd", 0.0)) + float(
            spent.get("this_run_usd", 0.0)
        )
        self._sources_path = root / "log_sources.json"
        self.known_sources: set[str] = set(
            json.loads(self._sources_path.read_text()) if self._sources_path.is_file() else []
        ) | {j.eval_set_id for j in self.ledger.jobs}

    def save_sources(self) -> None:
        self._sources_path.write_text(json.dumps(sorted(self.known_sources)))

    def check_model(self, model: str) -> None:
        if model not in self.worker_models:
            raise ToolError(
                f"{model!r} is not an allowed worker model. Allowed: {', '.join(self.worker_models)}"
            )

    def new_eval_set_id(self, label: str) -> str:
        """A fresh id per job. Reusing one makes Hawk resume that set instead."""
        return f"{self.policy.id_prefix}{slug(label)}-{uuid4().hex[:8]}"[:43]

    def model_costs(self) -> tuple[dict[str, dict[str, float]], list[str]]:
        """Prices for the worker models, so the runner can enforce its cost limit.

        The agent may not write these: a job whose prices are its own invention has a
        cost limit that means nothing. They come from the same registry the local
        allowance is accounted with.

        Both the key and the lookup use the name the job will run under. A worker is
        named in a config the way Hawk composes it, provider group then item, so
        `openai/gpt-5.6-luna` under the `openrouter` group is `openrouter/openai/
        gpt-5.6-luna` to Inspect, in the runner's cost table and in its logs. Returns
        the prices and the workers that have none: without a price a cost limit cannot
        bind, so that list is a refusal, not a warning.
        """
        from inspect_ai.model import get_model_info

        costs: dict[str, dict[str, float]] = {}
        missing: list[str] = []
        for model in self.worker_models:
            qualified = qualified_model_name(model)
            info = get_model_info(qualified)
            cost = info.cost if info else None
            if cost is None or not (cost.input or cost.output):
                missing.append(model)
                continue
            costs[qualified] = {
                "input": cost.input or 0.0,
                "output": cost.output or 0.0,
                "input_cache_read": cost.input_cache_read or 0.0,
                "input_cache_write": cost.input_cache_write or 0.0,
            }
        return costs, missing

    def record_local_spend(self) -> None:
        """Keep this run's own spend on disk, so a resumed investigation inherits it."""
        local = _local_spend()[0]
        if local is None:
            local = sample_limits().cost.usage
        self._spend_path.write_text(
            json.dumps({"prior_usd": self.prior_local_usd, "this_run_usd": local})
        )

    def local_usd(self) -> float:
        """Every dollar this investigation has spent on its own model calls."""
        local, _ = _local_spend()
        return self.prior_local_usd + (local if local is not None else sample_limits().cost.usage)

    def committed_usd(self) -> float:
        """Spent locally, plus collected remote costs, plus live reservations."""
        return self.local_usd() + self.ledger.actual_usd() + self.ledger.reserved_usd()

    def over_allowance(self) -> str | None:
        """The message to give the agent when the shared allowance is gone, else None."""
        _, unpriced = _local_spend()
        if unpriced:
            return "Cannot enforce the shared allowance: missing prices for " + ", ".join(unpriced)
        committed = self.committed_usd()
        if committed < self.allowance_usd:
            return None
        return (
            f"The ${self.allowance_usd:.2f} allowance is spent or committed "
            f"(${committed:.2f}: ${self.local_usd():.2f} on your own calls, "
            f"${self.ledger.actual_usd():.2f} measured on remote jobs, "
            f"${self.ledger.reserved_usd():.2f} held against jobs not yet collected)."
        )

    def reserve_and_record(self, job: Job) -> None:
        """Hold a job's worst case against the allowance and write it down, atomically.

        Both halves happen under the ledger's file lock, and the ledger is re-read
        inside it, so two submissions in flight cannot both take the last of the money.
        The job is written as `pending` before anything is sent to Hawk.
        """
        if _local_spend()[1]:
            raise ToolError("Cannot reserve more work while local model costs are unknown")
        with self.ledger.transaction() as ledger:
            existing = ledger.get(job.label)
            if existing is not None and existing.status != "failed":
                raise ToolError(
                    f"a job labelled {job.label!r} already exists; use jobs() on it or choose another name"
                )
            if existing is not None:
                # it never reached Hawk, so the name is free and its hold was released
                ledger.jobs.remove(existing)
            committed = self.committed_usd()
            if committed + job.reserved_usd > self.allowance_usd:
                raise ToolError(
                    f"cannot reserve ${job.reserved_usd:.2f}: ${committed:.2f} of the "
                    f"${self.allowance_usd:.2f} allowance is already spent or reserved. Collect "
                    "finished jobs to release their reservations, or make this job smaller "
                    "(fewer samples, a lower cost_limit)."
                )
            ledger.add(job)

    def settle(self, label: str, **fields: object) -> None:
        """Update one job under the lock; the ledger on disk is the record."""
        with self.ledger.transaction() as ledger:
            job = ledger.get(label)
            if job is None:  # pragma: no cover - only if the file was edited underneath us
                return
            for key, value in fields.items():
                setattr(job, key, value)

    async def reconcile(self) -> list[str]:
        """Resolve jobs left `pending` by a lost response or a killed process.

        A submission is written down before it is sent, so a job can be pending when
        Hawk never saw it, or when Hawk took it and the answer never came back. Asking
        Hawk which it was is the only way to know, and getting it wrong either loses a
        running job or launches it twice.
        """
        notes: list[str] = []
        for job in list(self.ledger.jobs):
            if job.status != "pending":
                continue
            try:
                exists = await self.hawk.eval_set_exists(job.eval_set_id)
            except Exception as ex:  # network or auth trouble: leave it pending
                notes.append(f"{job.label}: could not reach Hawk to check ({ex})")
                continue
            if exists:
                self.settle(job.label, status="submitted")
                self.known_sources.add(job.eval_set_id)
                self.save_sources()
                notes.append(f"{job.label}: was submitted after all ({job.eval_set_id})")
            else:
                self.settle(job.label, status="failed")
                notes.append(f"{job.label}: never reached Hawk; reservation released")
        return notes


def qualified_model_name(model: str) -> str:
    """The name a worker runs under: the OpenRouter group, then the model's own id."""
    return model if model.startswith("openrouter/") else f"openrouter/{model}"


def _models_named(config: dict[str, object]) -> set[str]:
    """Every model the config will actually construct, qualified as Hawk composes it."""
    named: set[str] = set()
    models = config.get("models")
    groups: list[object] = list(models) if isinstance(models, list) else []
    roles = config.get("model_roles")
    if isinstance(roles, dict):
        groups += list(roles.values())
    for group in groups:
        if not isinstance(group, dict):
            continue
        provider = str(group.get("name", ""))
        for item in group.get("items") or []:
            if isinstance(item, dict) and item.get("name"):
                named.add(f"{provider}/{item['name']}")
    return named


def _local_spend() -> tuple[float | None, list[str]]:
    """What this investigator has spent on its own calls, and what it could not price.

    The total is Inspect's own: `sample_limits().cost.usage` is the same number the
    cost limit is enforced against, so the tools and the limit cannot disagree. The
    per-model breakdown has no public equivalent, and it is only used to name the
    models whose price is missing, which is why an unpriced model makes the total
    unknown rather than merely smaller.
    """
    unpriced = [name for name, value in sample_model_usage().items() if value.total_cost is None]
    return (None if unpriced else sample_limits().cost.usage), unpriced


@tool(name="budget")
def investigation_budget(
    budget_usd: float, enforce_cost_limit: bool, remote: Remote | None = None
) -> Tool:
    """Expose Inspect's accounting without inventing a separate pricing system."""

    async def execute() -> str:
        """Show the allowance, spend so far by model, and tokens used."""
        usage = sample_model_usage()
        spend = sample_limits().cost
        lines = [f"Allowance: ${budget_usd:.2f}" + (" (enforced)" if enforce_cost_limit else " (planning only)")]
        total = spend.usage
        unpriced: list[str] = []
        for name, value in usage.items():
            cost = value.total_cost
            if cost is None:
                unpriced.append(name)
            lines.append(
                f"  {name}: in {value.input_tokens or 0:,} | cache read "
                f"{value.input_tokens_cache_read or 0:,} | out {value.output_tokens or 0:,}"
                f" (reasoning {value.reasoning_tokens or 0:,}) | "
                + (f"${cost:.2f}" if cost is not None else "cost unknown")
            )
        if unpriced:
            lines.append(
                f"Spent: at least ${total:.2f}; {', '.join(unpriced)} unpriced, so the "
                "true total and the remaining allowance are unknown"
            )
        else:
            remaining = spend.remaining
            lines.append(
                f"Spent: ${total:.2f}   Remaining: "
                + (f"${max(0.0, remaining):.2f}" if remaining is not None else "unlimited")
            )
        if remote is not None:
            remote.ledger.reload()
            jobs = remote.ledger.jobs
            lines.append(
                f"Remote jobs: {len(jobs)} launched, ${remote.ledger.actual_usd():.2f} measured cost, "
                f"${remote.ledger.reserved_usd():.2f} held against jobs not yet collected"
            )
            for j in jobs:
                cost_text = (
                    f"${j.actual_usd:.2f} spent"
                    if j.actual_usd is not None
                    else f"holds ${j.reserved_usd:.2f} (you estimated ${j.estimated_usd:.2f})"
                )
                lines.append(f"  {j.label} ({j.kind}, {j.eval_set_id}): {j.status}, {cost_text}")
            if remote.ledger.unpriced():
                lines.append(
                    "  collected but unpriced, so their real cost is unknown and their "
                    f"reservation is still held: {', '.join(remote.ledger.unpriced())}"
                )
            committed = remote.prior_local_usd + total + remote.ledger.actual_usd() + remote.ledger.reserved_usd()
            lines.append(f"Committed {'at least' if unpriced else 'in total'}: ${committed:.2f} of ${budget_usd:.2f}")
        lines.append(
            "Scope: this investigator's own model calls plus remote jobs it launched. The allowance is shared. Remote reservations include a buffer for model roles; Inspect's solver cost_limit does not cap scoring or in-flight overshoot."
        )
        return "\n".join(lines)

    return execute


@tool
def hawk_submit(remote: Remote, root: Path) -> Tool:
    """Submit an eval-set config you wrote to Hawk, after policy checks."""

    async def execute(config: str, estimated_usd: float, note: str | None) -> str:
        """Submit a Hawk eval-set config file from your workspace.

        Write the config yourself (see the investigating skill's examples, and Hawk's
        own documentation under /inputs/docs when the operator supplied it), save it
        under /workspace, and pass its path. It is parsed with Hawk's own schema and then checked against
        this investigation's policy: only the allowed packages, task packages, models,
        images, secrets and environment keys; task arguments that name a model, an
        image, a size or a log source must satisfy the same rules; `cost_limit` is
        required and capped; the job must state how many samples it runs. Anything
        else is refused with the reasons.

        The eval set id and the model prices are set here, not by you: a fresh id per
        job (a reused one makes Hawk resume that set) and the prices the allowance is
        accounted with, so the runner's cost limit means what it says. Your job holds
        its worst case (cost per sample x samples x models x epochs) against the
        allowance until jobs(action="collect").

        Args:
            config: Path of the YAML file in /workspace, e.g. /workspace/jobs/smoke.eval-set.yaml.
            estimated_usd: What you expect this to really cost. Recorded and compared
                with the outcome; the reservation is the worst case, not this number.
            note: Why you are running this; recorded in the ledger. Pass null for none.
        """
        if not config.startswith("/workspace/"):
            raise ToolError("config must be a path under /workspace")
        host_path = root / "work" / Path(config).relative_to("/workspace")
        if not host_path.is_file():
            raise ToolError(f"no such file: {config}")
        try:
            data = yaml.safe_load(host_path.read_text())
        except yaml.YAMLError as ex:
            raise ToolError(f"config is not valid YAML: {ex}") from ex
        if not isinstance(data, dict):
            raise ToolError("config must be a YAML mapping")
        if not (estimated_usd > 0):
            raise ToolError("estimated_usd must be positive: say what you expect this to cost")

        problems = validate_config(data, remote.policy, remote.known_sources)
        if problems:
            raise ToolError("config refused:\n- " + "\n- ".join(problems))

        label = str(data["name"]).removeprefix(remote.policy.id_prefix)
        eval_set_id = remote.new_eval_set_id(label)
        data["eval_set_id"] = eval_set_id
        costs, unpriced = remote.model_costs()
        named = _models_named(data)
        blind = sorted(named & {qualified_model_name(m) for m in unpriced})
        if blind:
            raise ToolError(
                f"no registered price for {', '.join(blind)}, so cost_limit could not be "
                "enforced in the runner and the job's spend would be unbounded. Use a model "
                "that is priced, or ask the operator to register a price for this one."
            )
        # every worker's price, not only the ones named here: a task that builds its
        # own grader still charges the same key, and an unpriced model is invisible to
        # the runner's cost limit
        data["model_cost_config"] = costs
        parsed, _ = parse_config(data)
        worst = worst_case_usd(parsed, remote.policy)
        if worst is None:  # pragma: no cover - validate_config already refused this
            raise ToolError("the job does not state its size")

        # the identity is claimed before anything is written: a refused duplicate must
        # not overwrite the config of the job that actually ran under that name
        submitted_path = root / "jobs" / f"{label}.eval-set.yaml"
        job = Job(
            label=label,
            kind="eval-set",
            eval_set_id=eval_set_id,
            config_path=str(submitted_path),
            submitted_at=utcnow(),
            estimated_usd=estimated_usd,
            reserved_usd=worst,
            status="pending",
            note=note or "",
        )
        replacing_failed = (remote.ledger.get(label) or Job("", "", "", "", "", 0)).status == "failed"
        if submitted_path.exists() and not replacing_failed:
            raise ToolError(f"{submitted_path.name} already exists; choose another name")
        remote.reserve_and_record(job)
        write_config(root, label, data)

        try:
            returned = await remote.hawk.submit(submitted_path)
        except Exception as ex:
            # the job is already written down as pending; ask Hawk whether it landed
            notes = await remote.reconcile()
            raise ToolError(
                f"submission failed: {ex}\n" + ("\n".join(notes) if notes else "")
            ) from ex
        if returned != eval_set_id:  # Hawk renamed it; the ledger follows Hawk
            eval_set_id = returned
        remote.settle(label, status="submitted", eval_set_id=eval_set_id)
        remote.known_sources.add(eval_set_id)
        remote.save_sources()
        return (
            f"Submitted {data['name']!r} as Hawk eval set {eval_set_id}. Reserved "
            f"${worst:.2f}, the most it can spend (you estimated ${estimated_usd:.2f}). "
            f"jobs(action='watch', label='{label}') shows it running; jobs(action='wait', "
            f"label='{label}') blocks until it finishes; jobs(action='collect', ...) brings "
            f"the logs to /inputs/jobs/{label}/ and releases what it did not spend."
        )

    return execute


@tool
def jobs(remote: Remote, root: Path) -> Tool:
    """Watching, reading, waiting on, collecting and stopping remote jobs."""

    async def execute(
        action: str,
        label: str | None,
        sample: str | None,
        wait_minutes: float | None,
        limit: int | None,
    ) -> str:
        """Watch and manage the Hawk jobs this investigation launched.

        Every action is the `hawk` command of the same name, run here on the operator's
        login, restricted to your own jobs. Reads are always safe; the only actions that
        change anything are "stop" and "collect".

        Args:
            action: What to do.
                "list" - every job of this investigation and its state (no network).
                "evals" - task, model, status and sample counts per eval in the job.
                "watch" - live snapshot: per-task and per-sample phase (waiting, init,
                    running, scoring, completed, errored, limit), retries, scores, and
                    any Kubernetes trouble reason. The first thing to look at when a job
                    is slow or stuck.
                "logs" - tail of the runner's own log: install failures, tracebacks, the
                    reason a job has no evals at all.
                "trace" - the runner's in-flight actions; an `enter` with no matching
                    `exit` is what it is blocked on right now. Running pod only.
                "stacktrace" - live thread stacks of the runner process. Running pod only.
                "status" - the raw monitoring report (pod status, metrics, recent logs).
                "samples" - one line per sample with its id, status and score.
                "transcript" - one sample's full transcript, written to
                    /inputs/jobs/<label>/transcripts/; pass `sample`.
                "transcripts" - every sample's transcript, written to the same place;
                    `limit` caps how many.
                "wait" - block, spending no tokens, until the job finishes or
                    wait_minutes pass.
                "collect" - download the job's .eval logs to /inputs/jobs/<label>/,
                    record the real cost, release the reservation.
                "stop" - gracefully stop a running job; completed samples are scored.
            label: Which job, for every action except "list".
            sample: Sample uuid, for action "transcript" (the "samples" action lists them).
            wait_minutes: How long "wait" may block before returning the current state;
                null means twenty.
            limit: For "samples" and "transcripts", how many samples to take; null means
                all of them. Pass null for every argument an action does not use.
        """
        ledger = remote.ledger
        if action == "list":
            if not ledger.jobs:
                return "no remote jobs yet"
            return "\n".join(
                f"{j.label} ({j.kind}) {j.eval_set_id}: {j.status}"
                + (f", collected to /inputs/jobs/{j.label}" if j.collected_to else "")
                + (f", ${j.actual_usd:.2f}" if j.actual_usd is not None else f", reserved ${j.estimated_usd:.2f}")
                for j in ledger.jobs
            )
        if not label or not (job := ledger.get(label)):
            raise ToolError(f"unknown job {label!r}; jobs(action='list') shows them")
        transcripts = root / "inputs" / "jobs" / label / "transcripts"
        try:
            if action == "logs":
                return f"{label} ({job.eval_set_id}) runner log tail:\n" + await remote.hawk.logs(
                    job.eval_set_id
                )
            if action == "watch":
                return f"{label} ({job.eval_set_id}) live status:\n" + await remote.hawk.watch(
                    job.eval_set_id
                )
            if action == "trace":
                return f"{label} ({job.eval_set_id}) runner trace:\n" + await remote.hawk.trace(
                    job.eval_set_id
                )
            if action == "stacktrace":
                return f"{label} ({job.eval_set_id}) runner stacks:\n" + await remote.hawk.stacktrace(
                    job.eval_set_id
                )
            if action == "status":
                return f"{label} ({job.eval_set_id}) monitoring report:\n" + await remote.hawk.status(
                    job.eval_set_id
                )
            if action == "samples":
                rows_json = await remote.hawk.samples(job.eval_set_id, limit)
                if not rows_json:
                    return f"{label}: no samples listed yet"
                out = [f"{len(rows_json)} sample(s) in {job.eval_set_id}:"]
                for row in rows_json:
                    out.append(
                        f"  {row.get('uuid', '?')} id={row.get('id', '')} "
                        f"epoch={row.get('epoch', '')} {row.get('status', '')} "
                        f"scores={row.get('scores', '')}"
                    )
                return "\n".join(out)
            if action == "transcript":
                if not sample:
                    raise ToolError("action='transcript' needs sample=<uuid> from jobs(action='samples')")
                # a sample uuid addresses any sample in the deployment, so membership in
                # this job is checked here rather than trusted from the argument
                known = await remote.hawk.samples(job.eval_set_id)
                if sample not in {str(row.get("uuid")) for row in known}:
                    raise ToolError(
                        f"sample {sample!r} is not in job {label!r}; jobs(action='samples', "
                        f"label='{label}') lists the ones you can read"
                    )
                path = await remote.hawk.transcript(sample, transcripts)
                return (
                    f"wrote /inputs/jobs/{label}/transcripts/{path.name} "
                    f"({path.stat().st_size:,} bytes). Read it with your own tools."
                )
            if action == "transcripts":
                files = await remote.hawk.transcripts(job.eval_set_id, transcripts, limit)
                if not files:
                    raise ToolError("no transcripts were written")
                return (
                    f"wrote {len(files)} transcript(s) to /inputs/jobs/{label}/transcripts/: "
                    + ", ".join(f.name for f in files[:10])
                    + (" …" if len(files) > 10 else "")
                )
            if action == "evals":
                rows = await remote.hawk.evals(job.eval_set_id)
            elif action == "wait":
                rows = await wait_for(remote.hawk, job.eval_set_id, wait_minutes or 20)
            elif action == "stop":
                await remote.hawk.stop(job.eval_set_id)
                remote.settle(label, status="stopped")
                return f"stop requested for {label} ({job.eval_set_id})"
            elif action == "collect":
                rows = await remote.hawk.evals(job.eval_set_id)
                if not rows or not all(r["status"] in ("success", "error", "cancelled") for r in rows):
                    state = ", ".join(f"{r['task']} {r['status']} {r['samples']}" for r in rows) or "no evals yet"
                    return (
                        f"{label} is not finished ({state}). No files downloaded. "
                        f"Use jobs(action='wait', label='{label}') before collecting. "
                        "If there are no evals, inspect watch/logs once for a startup failure."
                    )
                files = await remote.hawk.download(job.eval_set_id, root / "jobs" / "downloads" / label)
                if not files:
                    raise ToolError("no .eval files were downloaded")
                dest = copy_into_inputs(files, root / "inputs", label)
                cost, usage, recomputed = usage_cost(files)
                # an unpriced model leaves the real cost unknown: recording the
                # estimate here would turn a guess into a measurement, so the
                # reservation stands instead
                remote.settle(
                    label,
                    actual_usd=cost,
                    collected_to=str(dest),
                    evals=[dict(r) for r in rows],
                    status="success" if all(r["status"] == "success" for r in rows) else "error",
                )
                job = remote.ledger.get(label) or job
                lines = [f"collected {len(files)} log(s) to /inputs/jobs/{label}/"]
                lines += [f"  {r['task']} {r['model']}: {r['status']} {r['samples']}" for r in rows]
                lines.append(
                    (
                        f"cost: ${cost:.2f} "
                        + (
                            "(recomputed from today's prices, an estimate: the logs "
                            "recorded no cost)"
                            if recomputed
                            else "(as recorded by the runner)"
                        )
                        + f", reservation of ${job.reserved_usd:.2f} released"
                    )
                    if cost is not None
                    else f"cost unknown: a model in this job has no registered price, so the "
                    f"${job.reserved_usd:.2f} reservation stays held"
                )
                lines += [f"  {m}: in {u['input']:,} cache_read {u['cache_read']:,} out {u['output']:,}" for m, u in usage.items()]
                return "\n".join(lines)
            else:
                raise ToolError(
                    "action must be list, evals, watch, logs, trace, stacktrace, status, "
                    "samples, transcript, transcripts, wait, collect or stop"
                )
        except ToolError:
            raise
        except Exception as ex:
            message = str(ex)
            if "403" in message or "404" in message:
                raise ToolError(
                    f"Hawk has nothing to show for {label} ({job.eval_set_id}): it is "
                    f"{job.status}. A job that never started has no pod to watch and no "
                    "monitoring to report; jobs(action='list') shows what happened to it."
                ) from ex
            raise ToolError(f"hawk error: {ex}") from ex
        if rows:
            status = "success" if all(r["status"] == "success" for r in rows) else (
                "error" if any(r["status"] in ("error", "cancelled") for r in rows) and all(r["status"] in ("success", "error", "cancelled") for r in rows) else "running"
            )
            # every write goes through the lock: a bare save() here would rewrite the
            # whole file from a stale copy and could drop another process's reservation
            remote.settle(label, status=status, evals=[dict(r) for r in rows])
            job.status, job.evals = status, [dict(r) for r in rows]
        return f"{label} ({job.eval_set_id}): {job.status}\n" + "\n".join(
            f"  {r['task']} {r['model']}: {r['status']} {r['samples']}" for r in rows
        ) if rows else f"{label} ({job.eval_set_id}): no evals listed; use jobs(action='watch') or jobs(action='logs') to diagnose"

    return execute


@solver
def check_evidence_access(remote: Remote | None, root: Path, sources: list[str]) -> Solver:
    """Check supplied remote evidence before spending on the lead model."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        checks = []
        for address in sources:
            check = {"source": address, "status": "unavailable", "reason": ""}
            try:
                if remote is None or not address.startswith("hawk:"):
                    raise ValueError("No supported reader: supply an indexed Hawk eval set or local logs")
                eval_set = address.removeprefix("hawk:").split("/")[0]
                rows = await remote.hawk.samples(eval_set, 1)
                if not rows:
                    raise ValueError("No indexed samples. A storage prefix is not necessarily an imported eval set")
                await remote.hawk.transcript(
                    str(rows[0]["uuid"]), root / "inputs" / "index" / _alias(address) / "transcripts"
                )
                check.update(status="readable", reason="Sample index and one transcript retrieved; not a complete coverage check")
            except Exception as ex:
                check["reason"] = str(ex)[-1500:]
            checks.append(check)
        seed_path = root / "inputs" / "seed.json"
        seed = json.loads(seed_path.read_text())
        seed["evidence_access"] = checks
        seed_path.write_text(json.dumps(seed, indent=2))
        transcript().info(json.dumps({"evidence_access": checks}))
        return state

    return solve


def write_experiment_templates(remote: Remote, root: Path, target: str) -> None:
    """Generate starting configs from the active policy and validate with Hawk."""
    import copy

    if not remote.worker_models or "/" not in target:
        return
    package_name, task_name = target.split("/", 1)
    def model(name: str) -> dict[str, Any]:
        return {"package": "openai", "name": "openrouter", "items": [
            {"name": name, "args": {"base_url": "https://openrouter.ai/api/v1"}}
        ]}
    config: dict[str, Any] = {
        "name": "inv-benchmark-smoke", "packages": [remote.task_package],
        "tasks": [{"package": remote.task_package, "name": package_name,
                   "items": [{"name": task_name, "args": {}}]}],
        "models": [model(remote.worker_models[0])],
        "model_roles": {"grader": model(remote.worker_models[-1])},
        "runner": {"environment": {"HAWK_API_URL": remote.hawk_api_url,
                                    "HAWK_RUNNER_REFRESH_URL": ""},
                   "secrets": [{"name": "OPENROUTER_API_KEY"}]},
        "limit": 2, "epochs": 1, "cost_limit": 0.5,
        "token_limit": 200000, "working_limit": 600, "time_limit": 14400,
        "max_connections": 5, "max_retries": 3, "retry_attempts": 0,
    }
    config["model_roles"]["grader"]["items"][0]["args"]["config"] = {"max_tokens": 2048}
    audit = copy.deepcopy(config)
    audit.update(name="inv-audit-smoke", limit=1, cost_limit=2.0,
                 token_limit=2000000, working_limit=3600)
    audit["packages"].append(remote.audit_package)
    audit["tasks"] = [{"package": remote.audit_package, "name": "inspect_audit",
                       "items": [{"name": "audit", "args": {
                           "task": target, "items": ["gold-answer", "answer-format"],
                           "auditor_image": remote.auditor_image}}]}]
    destination = root / "work" / "jobs" / "templates"
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in [("benchmark", config), ("audit", audit)]:
        problems = validate_config(value, remote.policy, remote.known_sources)
        if problems:
            logger.warning("Skipping invalid %s convenience template: %s", name, problems)
            continue
        path = destination / f"{name}.yaml"
        if not path.exists():
            path.write_text(yaml.safe_dump(value, sort_keys=False))


@solver
def reconcile_jobs(remote: Remote) -> Solver:
    """Settle jobs an interrupted run left pending, before the agent does anything.

    A `setup` solver rather than task construction: it needs to talk to Hawk, the
    sample it belongs to is running by then, and what it finds belongs in that
    sample's transcript rather than in a log line nobody reads.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        for note in await remote.reconcile():
            transcript().info(f"resume: {note}")
        return state

    return solve


def _alias(source: str) -> str:
    """A short, filesystem-safe, unique name for a log source the agent can type.

    Truncating an address to thirty characters made two runs of the same benchmark the
    same name, and the second silently replaced the first in the mapping. The digest
    keeps them apart without making the name unreadable.
    """
    import hashlib

    stem = slug(source.removeprefix("hawk:").removeprefix("s3://").replace("/", "-")) or "logs"
    digest = hashlib.sha256(source.encode()).hexdigest()[:6]
    return f"{stem[:24]}-{digest}"


@tool(name="logs")
def supplied_logs(remote: Remote | None, root: Path, sources: list[str]) -> Tool:
    """Read log sources that are not on the agent's filesystem."""

    async def execute(
        action: str, source: str | None, sample: str | None, limit: int | None
    ) -> str:
        """Read the recorded runs that live somewhere else.

        Logs supplied as an address are not copied into this box: a benchmark's logs
        can be tens of gigabytes, and almost all of that is transcripts nobody reads.
        This reads them where they are, through the operator's credentials, and brings
        back only what you ask for. Logs supplied as files are already under
        /inputs/logs and want no tool at all.

        Args:
            action: "list" (the sources this investigation has, and which are files),
                "samples" (one row per recorded attempt: model, status, every scorer's
                value, tokens, timings, whether it hit a limit; written to a CSV under
                /inputs/index/ for you to load, with a summary returned), "transcript"
                (one attempt in full, written under /inputs/index/<source>/, needs
                `sample`), or "fetch" (download the whole set's .eval logs to
                /inputs/index/<source>/logs; say why, they are large).
            source: Which source, from action="list". Required except for "list".
            sample: The attempt's uuid, from the samples table, for "transcript".
            limit: How many samples to read for "samples"; null means the complete population. Pass
                null for every argument an action does not use.
        """
        known = {_alias(s): s for s in sources}
        if action == "list":
            if not known:
                return "no supplied log sources; the logs given to you are files under /inputs/logs"
            lines = ["source                          address"]
            lines += [f"{alias:31} {address}" for alias, address in sorted(known.items())]
            lines.append("Files under /inputs/logs are read directly; these are not.")
            return "\n".join(lines)
        if source not in known:
            raise ToolError(
                f"unknown log source {source!r}. This investigation may read: "
                + (", ".join(sorted(known)) or "none")
            )
        address = known[source]
        if remote is None or not address.startswith("hawk:"):
            raise ToolError(
                f"{source} is at {address}, which this investigation cannot read for you. "
                "Read the files under /inputs/logs, or ask the operator to supply it as a "
                "Hawk eval set."
            )
        eval_set, _, within = address.removeprefix("hawk:").partition("/")
        destination = root / "inputs" / "index" / source
        # the warehouse indexes samples by eval set, not by prefix: an address naming a
        # subdirectory still lists the whole set, and the agent is told so rather than
        # left to assume the narrower scope it asked for
        scope = (
            f"\nNote: {address} names {within!r} inside eval set {eval_set}; the "
            "warehouse lists samples per eval set, so this covers the whole set."
            if within
            else ""
        )
        try:
            if action == "samples":
                rows = await remote.hawk.samples(eval_set, limit)
                if not rows:
                    raise ToolError(f"{source}: no accessible indexed samples; this may be an indexing or permission problem, not an empty benchmark")
                destination.mkdir(parents=True, exist_ok=True)
                table = destination / "samples.csv"
                _write_samples_csv(rows, table)
                return (
                    f"{len(rows)} sample(s) in {source}, written to "
                    f"/inputs/index/{source}/samples.csv\n" + _samples_summary(rows) + scope
                )
            if action == "transcript":
                if not sample:
                    raise ToolError("action='transcript' needs sample=<uuid> from the samples table")
                if not await remote.hawk.has_sample(eval_set, sample):
                    raise ToolError(f"sample {sample!r} is not in {source}")
                path = await remote.hawk.transcript(sample, destination / "transcripts")
                return f"wrote /inputs/index/{source}/transcripts/{path.name} ({path.stat().st_size:,} bytes)"
            if action == "fetch":
                files = await remote.hawk.download(eval_set, destination / "logs")
                size = sum(f.stat().st_size for f in files)
                return (
                    f"downloaded {len(files)} log(s), {size / 1e6:,.0f} MB, to "
                    f"/inputs/index/{source}/logs/"
                )
            raise ToolError("action must be list, samples, transcript or fetch")
        except ToolError:
            raise
        except Exception as ex:
            raise ToolError(f"reading {source} failed: {ex}") from ex

    return execute


def _write_samples_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """One row per attempt, with each scorer as its own column."""
    import csv

    scorers = sorted({s.get("scorer", "") for r in rows for s in (r.get("scores") or [])})
    columns = [
        "uuid", "id", "epoch", "model", "task_name", "status", "limit", "error_message",
        "input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
        "message_count", "action_count", "total_time_seconds", "is_invalid",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns + scorers)
        for row in rows:
            scored = {s.get("scorer"): s.get("value") for s in (row.get("scores") or [])}
            writer.writerow(
                [row.get(c) for c in columns] + [scored.get(s) for s in scorers]
            )


def _samples_summary(rows: list[dict[str, Any]]) -> str:
    """Enough of the population to know what to look at next, in the tool result."""
    from collections import Counter

    lines: list[str] = []
    for field in ("model", "status", "limit"):
        counts = Counter(str(r.get(field)) for r in rows if r.get(field) is not None)
        if counts:
            lines.append(f"  {field}: " + ", ".join(f"{k} {v}" for k, v in counts.most_common(8)))
    grades: dict[str, Counter[str]] = {}
    for row in rows:
        for score in row.get("scores") or []:
            grades.setdefault(str(score.get("scorer")), Counter())[str(score.get("value"))] += 1
    for scorer, counts in sorted(grades.items()):
        lines.append(f"  {scorer}: " + ", ".join(f"{k} {v}" for k, v in counts.most_common(8)))
    errored = [r for r in rows if r.get("error_message")]
    if errored:
        lines.append(f"  errors: {len(errored)}, e.g. {str(errored[0]['error_message'])[:80]}")
    return "\n".join(lines)


async def _continue(
    state: AgentState, interactive: bool, remote: Remote | None = None
) -> bool | str:
    over = remote.over_allowance() if remote is not None else None
    if over is not None:
        # Inspect's cost limit only sees this agent's own calls, so a run whose children
        # hold most of the allowance would otherwise carry on spending locally as though
        # the money were still there. Ask once; if the agent has already published, or
        # asking did not stop it, end the sample the way Inspect ends any limit: the run
        # is recorded as having hit a limit, and what exists is still scored.
        investigation = store_as(InvestigationState)
        if investigation.published or investigation.allowance_notified:
            raise LimitExceededError(
                "custom",
                value=remote.committed_usd() if remote else 0.0,
                limit=remote.allowance_usd if remote else 0.0,
                message=over,
            )
        investigation.allowance_notified = True
        return (
            f"{over} Publish the report now with what you have, and say in it what you "
            "could not finish and why. This is your last chance to write it."
        )
    if not store_as(InvestigationState).published:
        return (
            True
            if state.output.message.tool_calls
            else "Continue the investigation, or write and publish a report explaining the evidence and any limitations."
        )
    if not interactive:
        return False
    return await _operator_turn(state)


def _resumable(resume: str) -> Path:
    """An existing investigation directory, with its inputs, workspace and ledger."""
    root = Path(resume).expanduser().resolve()
    if not (root / "inputs" / "seed.json").is_file() or not (root / "work").is_dir():
        raise ValueError(
            f"{resume} is not an investigation directory: it has no inputs/seed.json and work/"
        )
    return root


@task
def investigate(
    repo: str | None = None,
    config: str | None = None,
    logs: list[str] | None = None,
    paper: str | None = None,
    docs: list[str] | None = None,
    overview: str | None = None,
    target_task: str | None = None,
    revision: str | None = None,
    paths: list[str] | None = None,
    output_dir: str | None = None,
    resume: str | None = None,
    budget_usd: float | None = None,
    enforce_cost_limit: bool | None = None,
    token_limit: str | int | None = None,
    interactive: bool | None = None,
    extra_skills: list[str] | None = None,
    hawk_api_url: str | None = None,
    audit_package: str | None = None,
    auditor_image: str | None = None,
    worker_models: list[str] | None = None,
    secrets_file: str | None = None,
    log_bucket: str | None = None,
    aws_profile: str | None = None,
) -> Task:
    """Investigate source and existing logs locally, publish HTML, then discuss.

    Requires Docker and, when interactive, --acp-server. Benchmark execution and
    Hawk job dispatch are deliberately not enabled in this first slice.

    Args:
        repo: Local Git repository or HTTPS Git URL of the benchmark.
        config: YAML file setting any of these arguments, so an investigation can be a
            document rather than a command line. Anything also passed with -T wins.
        logs: Inspect log files or directories (hardlinked, read-only in the box).
        paper: Local file or URL; a URL is downloaded now (arXiv abs -> pdf). Defaults to
            the paper the eval names in its own metadata.
        docs: Documentation directories to mount read-only (inspect docs, Hawk docs).
        overview: Optional operator steer.
        target_task: The task under audit, e.g. `inspect_evals/simpleqa_verified`.
            Defaults to the only task the repository declares, when there is one.
        revision: Commit to snapshot (default HEAD).
        paths: Repository paths to include in the snapshot. Defaults to the audited
            task's own directory and the modules its package shares, from the eval's
            metadata; everything, when that cannot be determined.
        output_dir: Where the investigation directory is created.
        resume: An existing investigation directory to carry on in, instead of creating
            one. Its inputs, workspace, journal and job ledger are reused, supplied logs
            are not staged again, and jobs left pending by an interrupted run are
            reconciled with Hawk before the agent starts.
        budget_usd: Dollar allowance for this investigator's own model calls.
        enforce_cost_limit: Enforce `budget_usd` through Inspect's cost limit. Needs a
            price for the model; OpenRouter prices are registered automatically.
        token_limit: Optional Inspect token limit (e.g. "output:500k"); none by default.
        interactive: Wait for the operator over ACP after publishing.
        extra_skills: Additional skill directories to load.
        hawk_api_url: Enable remote work through Hawk at this API, defaulting to
            HAWK_API_URL. The `hawk` CLI must be installed and logged in on this machine;
            its tools run here, never in the box.
        audit_package: git spec of inspect_audit for sample-audit jobs. Defaults to the
            commit this process is running.
        auditor_image: Published auditor image for sample-audit jobs on k8s.
        worker_models: OpenRouter model ids the agent may run (benchmark workers, auditors,
            graders). Prices for these are registered so costs are accounted.
        secrets_file: .env passed to Hawk jobs (OPENROUTER_API_KEY); never read by the
            agent. Defaults to the .env Inspect itself loaded, found from the working
            directory upwards.
        log_bucket: Retained for compatibility; local logs are no longer uploaded.
        aws_profile: Retained for compatibility; job-readable inputs use native Hawk import.
    """
    # the file is read before anything else is decided: a setting it carries must be
    # able to change what gets validated, which skills load and how much may be spent.
    # An argument is "given" only when it is not None, so a value equal to a default is
    # still an override.
    settings = _investigation_file(config, locals()) if config else {}
    repo = settings.get("repo", repo)
    logs = settings.get("logs", logs)
    paper = settings.get("paper", paper)
    docs = settings.get("docs", docs)
    overview = settings.get("overview", overview)
    target_task = settings.get("target_task", target_task)
    revision = settings.get("revision", revision)
    paths = settings.get("paths", paths)
    output_dir = settings.get("output_dir", output_dir)
    resume = settings.get("resume", resume)
    budget_usd = settings.get("budget_usd", budget_usd)
    enforce_cost_limit = settings.get("enforce_cost_limit", enforce_cost_limit)
    token_limit = settings.get("token_limit", token_limit)
    interactive = settings.get("interactive", interactive)
    extra_skills = settings.get("extra_skills", extra_skills)
    hawk_api_url = settings.get("hawk_api_url", hawk_api_url)
    audit_package = settings.get("audit_package", audit_package)
    auditor_image = settings.get("auditor_image", auditor_image)
    worker_models = settings.get("worker_models", worker_models)
    secrets_file = settings.get("secrets_file", secrets_file)

    overview = overview if overview is not None else ""
    output_dir = output_dir if output_dir is not None else "investigations"
    budget_usd = budget_usd if budget_usd is not None else 10.0
    enforce_cost_limit = enforce_cost_limit if enforce_cost_limit is not None else True
    interactive = bool(interactive)
    auditor_image = auditor_image or DEFAULT_AUDITOR_IMAGE

    if not math.isfinite(budget_usd) or budget_usd <= 0:
        raise ValueError("budget_usd must be finite and positive")
    skill_paths = [str(ASSETS / "skills" / name) for name in INVESTIGATION_SKILLS] + [
        str(SKILLS / name) for name in SUPPORT_SKILLS
    ]
    for path in extra_skills or []:
        resolved = Path(path).expanduser().resolve()
        if not (resolved / "SKILL.md").is_file():
            raise ValueError(f"Expected a skill directory containing SKILL.md: {path}")
        skill_paths.append(str(resolved))
    if not repo:
        raise ValueError("investigate needs a repo: the benchmark to audit, or a config naming one")

    resumed = _resumable(resume) if resume else None
    if resumed and revision is None:
        # the snapshot is not retaken, so the commit a runner installs is the one that
        # snapshot was taken at, not wherever the checkout has moved to since
        revision = str(
            json.loads((resumed / "inputs" / "seed.json").read_text()).get("revision") or ""
        ) or None

    # what can be worked out is worked out: the audited repository already says which
    # commit it is, which directory the task lives in and which paper it comes from,
    # and this package already knows its own commit. Passing any of them overrides.
    local_repo = Path(repo).expanduser()
    if local_repo.is_dir():
        target_task = target_task or _only_task(local_repo)
    # the provider key reaches a Hawk runner from a file. Look beside the investigation
    # first, then beside the benchmark, then where Inspect itself looks: a run launched
    # from a worktree found nothing there and every job it started failed with 401.
    secrets_file = secrets_file or _find_secrets(config, local_repo)
    if local_repo.is_dir():
        paths = paths or paths_from_metadata(local_repo, target_task)
        paper = paper or paper_from_metadata(local_repo, target_task)
    hawk_api_url = hawk_api_url or os.environ.get("HAWK_API_URL")
    task_package: str | None = None
    if hawk_api_url:
        # a runner installs the benchmark from git: a local checkout supplies its own
        # origin and commit, and a repository given as a URL is already that answer
        task_package = (
            git_package_spec(local_repo, revision)
            if local_repo.is_dir()
            else f"git+{repo.removesuffix('.git')}@{revision}"
            if revision
            else None
        )
        audit_package = audit_package or own_package_spec()
        if not secrets_file:
            raise ValueError(
                "remote work needs a secrets file holding OPENROUTER_API_KEY: a Hawk "
                "runner has no environment of yours, and every job would fail to "
                "authenticate. Looked beside the config, beside the repository, and "
                "upwards from here. Pass secrets_file."
            )
        if not task_package:
            raise ValueError(
                "remote work installs the benchmark in a Hawk runner from git, and this "
                "repository cannot say where from. Give a checkout with an origin remote, "
                "or an https git URL with a revision."
            )
        if not audit_package:
            raise ValueError(
                "remote work needs the pip spec that installs inspect_audit in a Hawk "
                "runner, and this install is not one git can describe. Pass audit_package."
            )
    if enforce_cost_limit or hawk_api_url:
        register_openrouter_costs()
    root = resumed or prepare_workspace(
        repo,
        revision,
        paths,
        logs or [],
        paper,
        docs or [],
        overview,
        target_task,
        output_dir,
        budget_usd,
    )

    remote: Remote | None = None
    if hawk_api_url:
        remote = Remote(
            root, hawk_api_url, secrets_file, task_package or "", audit_package or "", auditor_image,
            worker_models or DEFAULT_WORKERS, budget_usd,
        )
        seed_path = root / "inputs" / "seed.json"
        seed = json.loads(seed_path.read_text())
        # Local evidence stays local. A remote worker receives only a source
        # created by Hawk's native import or a previous Hawk evaluation.
        parked = [str(entry["remote"]) for entry in seed["logs"] if entry.get("remote")]
        for address in parked:
            remote.known_sources.add(address)
        if target_task and "/" in target_task:
            remote.policy.task_names.append(target_task.split("/", 1)[0])
        remote.save_sources()
        seed["remote"] = {
            "hawk": hawk_api_url,
            "task_package": task_package,
            "worker_models": worker_models or DEFAULT_WORKERS,
            "audit_package": audit_package,
            "auditor_image": auditor_image,
            # every remote log source explicitly supplied by the operator
            "supplied_logs": parked,
            "note": (
                "Local /inputs/logs files are for local analysis only. Remote audits need an "
                "operator-imported Hawk source (hawk import), listed in supplied_logs. "
                "Write an eval-set config under /workspace and hawk_submit it. To audit the "
                "supplied logs, pass one of supplied_logs as the audit task's logs argument; to audit a "
                "job you ran, use hawk:<its eval set id>. Do not set eval_set_id: submission "
                "assigns a fresh one, and logs are fetched through the Hawk API, so a job reads "
                "them whatever its own id is. Every config states cost_limit, the dollars a "
                "single sample may spend, and its size; the two decide what the job holds "
                "against the allowance. jobs() watches, waits, collects into "
                "/inputs/jobs/<label>/ and shows runner logs, traces and transcripts."
            ),
        }
        seed_path.write_text(json.dumps(seed, indent=2))
    setup_steps: list[Solver] = []
    if remote is not None:
        if resumed:
            setup_steps.append(reconcile_jobs(remote))
    tools: list[Tool] = [
        bash(timeout=300),
        skill(skill_paths),
        investigation_budget(budget_usd, enforce_cost_limit, remote),
        render_report(),
        view_image(),
        publish_report(str(root)),
    ]
    seed_logs = json.loads((root / "inputs" / "seed.json").read_text()).get("logs") or []
    remote_sources = [str(e["remote"]) for e in seed_logs if isinstance(e, dict) and e.get("remote")]
    if remote_sources:
        setup_steps.append(check_evidence_access(remote, root, remote_sources))
        tools.append(supplied_logs(remote, root, remote_sources))
    if remote is not None:
        if target_task:
            write_experiment_templates(remote, root, target_task)
        tools += [hawk_submit(remote, root), jobs(remote, root)]

    async def on_continue(state: AgentState) -> bool | str:
        if remote is not None:
            remote.record_local_spend()
        return await _continue(state, interactive, remote)

    return Task(
        setup=setup_steps or None,
        dataset=[
            Sample(
                id="investigation",
                input="Read /inputs/seed.json and invoke the investigating skill. Begin the investigation autonomously.",
            )
        ],
        solver=react(
            name="investigator",
            prompt=prompts.INVESTIGATE,
            submit=False,
            tools=tools,
            compaction=CompactionSummary(threshold=0.8),
            truncation="auto",
            on_continue=on_continue,
        ),
        sandbox=("docker", str(root / "compose.yaml")),
        # tool results are truncated at 16KB by default; an inventory of fifty
        # logs or a transcript dump is routinely larger, and a truncated view
        # is what the agent then reasons from
        config=GenerateConfig(max_tool_output=200 * 1024),
        cost_limit=budget_usd if enforce_cost_limit else None,
        token_limit=token_limit,
        working_limit=4 * 3600,
        metadata={
            "investigation_dir": str(root),
            "interactive": interactive,
            "capabilities": ["repository", "existing_logs", "docs", "quarto_report", "acp"]
            + (["hawk_jobs"] if remote else []),
        },
    )
