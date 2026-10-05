"""Legacy conversational synthesis task, retained for existing ACP clients.

New benchmark investigations use inspect_audit/investigate. This module only
stages existing audit logs for a conversation; it does not publish a report bundle.
"""
import atexit
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path

from inspect_ai import Task
from inspect_ai.agent import react
from inspect_ai.dataset import Sample
from inspect_ai.log import list_eval_logs
from inspect_ai.tool import bash, python, skill
from inspect_ai.util import SandboxEnvironmentType

from . import prompts
from ._agent import SKILLS, SUPPORT_SKILLS
from ._report import _operator_turn
from .containers import COMPOSE, DOCKERFILE

REPORT_ROOT = "/report"
REPORT_SKILLS = Path(__file__).parent / "report_skills"


def _staged_logs(logs: str) -> dict[str, str]:
    """Container path -> host path for every log under `logs`.

    Uses `list_eval_logs` (which knows what a log file is) and keeps each
    file's path relative to the log root, so same-named logs in different
    subdirectories cannot silently collide.
    """
    root = Path(logs).resolve()
    if root.is_file():
        return {f"{REPORT_ROOT}/logs/{root.name}": str(root)}
    files = [
        Path(info.name.removeprefix("file://")).resolve()
        for info in list_eval_logs(str(root))
    ]
    if not files:
        raise ValueError(f"No logs found at {logs!r}.")
    return {f"{REPORT_ROOT}/logs/{f.relative_to(root)}": str(f) for f in files}


def _report_sandbox() -> SandboxEnvironmentType:
    """The audit module's generic sandbox, with the log-reading stack installed."""
    stage = Path(tempfile.mkdtemp(prefix="inspect_report_sandbox_"))
    atexit.register(shutil.rmtree, stage, ignore_errors=True)
    requirements = f"inspect-ai=={version('inspect-ai')} pandas pyarrow"
    (stage / "Dockerfile").write_text(DOCKERFILE.format(requirements=requirements))
    compose = stage / "compose.yaml"
    compose.write_text(COMPOSE)
    return ("docker", str(compose))


def report_task(logs: str | None = None) -> Task:
    """Build the synthesis session as an Inspect `Task`.

    Args:
        logs: Log file or directory of logs to stage into the sandbox at
            /report/logs. None runs the sandbox-less chat skeleton.
    """
    if logs is None:
        return Task(
            dataset=[
                Sample(
                    input="Greet the operator in one short sentence and wait "
                    "for direction."
                )
            ],
            solver=react(
                name="report",
                description="Synthesis agent (plumbing-test mode).",
                prompt=prompts.REPORT_CHAT_ONLY,
                on_continue=_operator_turn,
            ),
        )

    files = _staged_logs(logs)
    intro = (
        f"{len(files)} log file(s) are staged at {REPORT_ROOT}/logs. Greet the "
        "operator in one short sentence and wait for direction."
    )
    return Task(
        dataset=[Sample(input=intro, files=files)],
        solver=react(
            name="report",
            description="Synthesis agent over completed audit logs.",
            prompt=prompts.REPORT.format(root=REPORT_ROOT),
            tools=[
                bash(timeout=300),
                python(timeout=300),
                skill(
                    [str(d) for d in sorted(REPORT_SKILLS.iterdir()) if d.is_dir()]
                    + [str(SKILLS / name) for name in SUPPORT_SKILLS]
                ),
            ],
            on_continue=_operator_turn,
        ),
        sandbox=_report_sandbox(),
    )


