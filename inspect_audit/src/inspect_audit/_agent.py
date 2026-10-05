import base64
import json
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from typing import Any, cast

from inspect_ai.agent import Agent, AgentSubmit, agent, react
from inspect_ai.model import GenerateConfig, Model, get_model
from inspect_ai.scorer import (
    Score,
    Scorer,
    Target,
    frequency,
    scorer,
)
from inspect_ai.solver import TaskState
from inspect_ai.solver._task_state import sample_state
from inspect_ai.tool import (
    ContentImage,
    Tool,
    ToolDef,
    ToolError,
    ToolParam,
    ToolResult,
    bash,
    python,
    read_skills,
    skill,
    tool,
)
from inspect_ai.tool._tools._execute import code_viewer
from inspect_ai.util import (
    LimitExceededError,
    StoreModel,
    sandbox,
    sandbox_default,
    store_as,
)
from PIL import Image
from pydantic import BaseModel, Field, JsonValue

from . import prompts
from ._contract import SolverContract
from ._item import AUDIT_ROOT
from ._sandbox import (
    BENCHMARK_SERVICE,
    benchmark_boxes,
    has_benchmark_box,
    phoenix_benchmark,
    restore_benchmark,
)
from ._state import BenchmarkState, attempt, benchmark_task_state, benchmark_tools

SKILLS = Path(__file__).parent / "skills"

# support skills help with the work rather than defining it; an auditor is never
# asked to investigate one of these
SUPPORT_SKILLS = ("reading-logs", "analyzing-logs", "map-inspect-packages")





class AuditItemSkill(BaseModel):
    """One audit item, read from its skill's frontmatter."""

    name: str
    description: str
    grades: list[str]
    unevidenced: list[str] = Field(default_factory=list)
    details: dict[str, str] = Field(default_factory=dict)
    """Detail fields this item requires with a verdict, as name -> description."""
    tools: list[str] = Field(default_factory=list)
    """Mutating benchmark tools this item grants the auditor, e.g. `attempt`,
    `reset`. `grade` needs no grant: it is granted whenever the benchmark has
    a grader."""


# tools an item's frontmatter may grant. `grade` is listed for legibility only:
# it is granted whenever the benchmark has a grader (see `auditor_tools`).
_GRANTABLE_TOOLS = frozenset({"attempt", "grade", "reset"})


def _item_skill(
    name: str, description: str, metadata: dict[str, Any]
) -> AuditItemSkill:
    """Read one item skill's frontmatter contract, loudly.

    The frontmatter drives grade validation, evidence rules and tool grants, so
    a malformed block must fail here at load. Coerced to an empty contract it
    would instead surface at verdict time, as `record_verdict` rejecting every
    grade forever -- a paid run burned against its limits with no verdict.
    """

    def str_list(key: str) -> list[str]:
        value = metadata.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(
                f"audit skill {name!r}: frontmatter `{key}` must be a list of "
                f"strings, got {value!r}"
            )
        return value

    grades = str_list("grades")
    if not grades:
        raise ValueError(
            f"audit skill {name!r} declares no grades: its frontmatter needs "
            "`metadata.grades`, the exhaustive list of verdicts it can produce"
        )
    unevidenced = str_list("unevidenced")
    if rogue := set(unevidenced) - set(grades):
        raise ValueError(
            f"audit skill {name!r}: `unevidenced` names grades it does not "
            f"declare: {', '.join(sorted(rogue))}"
        )
    tools = str_list("tools")
    if unknown := set(tools) - _GRANTABLE_TOOLS:
        raise ValueError(
            f"audit skill {name!r}: `tools` grants unknown tools "
            f"{', '.join(sorted(unknown))} "
            f"(grantable: {', '.join(sorted(_GRANTABLE_TOOLS))})"
        )
    declared = metadata.get("details", {})
    if not isinstance(declared, dict):
        raise ValueError(
            f"audit skill {name!r}: frontmatter `details` must be a mapping of "
            f"field name to description, got {declared!r}"
        )
    return AuditItemSkill(
        name=name,
        description=description,
        grades=grades,
        unevidenced=unevidenced,
        details={str(k): str(v) for k, v in declared.items()},
        tools=tools,
    )


def audit_items(items: list[str] | None = None) -> list[AuditItemSkill]:
    """The audit items an auditor can investigate, one skill each.

    Args:
        items: Restrict to these item names (defaults to all of them).

    Raises:
        ValueError: A skill's frontmatter contract is malformed, or `items`
            names an unknown item.
    """
    dirs = [
        path
        for path in sorted(SKILLS.iterdir())
        if path.is_dir() and path.name not in SUPPORT_SKILLS
    ]
    read = [
        _item_skill(s.name, s.description, s.metadata or {})
        for s in read_skills([str(d) for d in dirs])
    ]
    if items is not None:
        known = {s.name for s in read}
        unknown = set(items) - known
        if unknown:
            raise ValueError(
                f"Unknown audit item(s) {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(known))}."
            )
        read = [s for s in read if s.name in items]
    return read


class Evidence(BaseModel):
    """One observation and its provenance: a url, a log path, or a command run."""

    observed: str
    source: str


class Verdict(BaseModel):
    """An auditor's verdict on one audit item."""

    grade: str
    approaches: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    tried: str | None = None
    remarks: str | None = None


class Verdicts(StoreModel):
    """Recorded verdicts, keyed by audit item name."""

    verdicts: dict[str, Verdict] = Field(default_factory=dict)
    debrief: dict[str, list[Evidence]] = Field(default_factory=dict)


@tool
def record_verdict(items: list[AuditItemSkill]) -> Tool:
    lookup = {item.name: item for item in items}

    async def execute(
        item: str,
        evidence: list[Evidence],
        approaches: str,
        tried: str,
        remarks: str,
        grade: str,
        details: str,
    ) -> str:
        """Record your verdict on one audit item.

        Args:
            item: The audit item this verdict is for.
            evidence: Verbatim observations establishing the grade, each with its
                source. Record what you observed; do not summarise what you concluded.
            approaches: One sentence per attempt: how it approached the task, and
                whether that path was the intended one.
            tried: What you did to try to break the item, including what failed.
            remarks: What you actually think, including anything you were not asked about.
            grade: Your grade for this item.
            details: JSON-encoded object containing the required fields listed for
                this item. Use "{}" when no fields are required.
        """
        # a ToolError is fed back to the model as recoverable, so a submission that
        # misses the contract becomes a retry rather than a lost verdict
        skill = lookup.get(item)
        if skill is None:
            raise ToolError(
                f"Unknown item {item!r}. Expected one of {', '.join(lookup)}."
            )
        try:
            # Accept dictionaries from existing Python callers, while the model-facing
            # schema uses a string: arbitrary objects cannot use OpenAI strict schemas.
            recorded_details = json.loads(details) if isinstance(details, str) else details
        except ValueError as ex:
            raise ToolError("details must encode a valid JSON object") from ex
        if not isinstance(recorded_details, dict):
            raise ToolError("details must encode a JSON object")
        if grade not in skill.grades:
            raise ToolError(
                f"Grade for {item} must be one of {', '.join(skill.grades)}."
            )
        if not evidence and grade not in skill.unevidenced:
            raise ToolError(
                f"A grade of {grade} needs at least one observation with its source."
            )
        for entry in evidence:
            if not entry.observed.strip() or not entry.source.strip():
                raise ToolError(
                    "Every piece of evidence needs both an observation and its source."
                )
        missing = [key for key in skill.details if key not in recorded_details]
        if missing:
            asks = ", ".join(f"{key} ({skill.details[key]})" for key in missing)
            raise ToolError(f"This item also requires details: {asks}.")

        # replace rather than mutate so the store sees the change
        recorded = store_as(Verdicts)
        recorded.verdicts = {
            **recorded.verdicts,
            item: Verdict(
                grade=grade,
                approaches=approaches,
                evidence=evidence,
                details=recorded_details,
                tried=tried,
                remarks=remarks,
            ),
        }
        return json.dumps({"item": item, "grade": grade})

    definition = ToolDef(execute, name="record_verdict")
    fields: dict[str, list[str]] = {}
    for item in items:
        for key, description in item.details.items():
            fields.setdefault(key, []).append(f"{item.name}: {description}")
    definition.parameters.properties["details"] = ToolParam(
        type="string",
        description="JSON-encoded object. Required fields by item:\n"
        + "\n".join(
            f"{item.name}: {', '.join(item.details) or '(none)'}" for item in items
        ),
    )
    # Keep arbitrary nested skill data inside the JSON string and validate it above.
    details = definition.parameters.properties["details"]
    details.description = (details.description or "") + "\n" + "\n".join(
        f"{key}: {'; '.join(descriptions)}" for key, descriptions in fields.items()
    )
    return definition.as_tool()


# render probe calls as a bash code block and let independent probes run
# concurrently, matching the bash tool this replaced (a `service` arg is the
# only reason it isn't just `ToolDef(bash(...))`)
@tool(viewer=code_viewer("bash", "cmd"), parallel=True)
def audit_probe() -> Tool:
    async def execute(cmd: str, service: str) -> str:
        """Look inside one of the benchmark's own containers, off the record.

        Runs a bash command in the named benchmark box, exactly as the
        evaluated agent's environment stands, WITHOUT recording anything as
        the agent's doing. Anything you download, write or install there is
        evidence about you, not about the environment. To act AS the agent --
        building the attempt a grader will judge -- use the benchmark_* tools.

        Args:
            cmd: The bash command line to run.
            service: Which box to probe. Usually `benchmark` (the one the
                evaluated agent held); a multi-service benchmark also has its
                sibling services, addressable by name. An unknown name is
                rejected with the list of this item's boxes.
        """
        boxes = benchmark_boxes()
        if not boxes:
            raise ToolError("This item has no benchmark environment to probe.")
        if service not in boxes:
            raise ToolError(
                f"Unknown service {service!r}. This item's benchmark boxes: "
                f"{', '.join(sorted(boxes))}."
            )
        # `bash --login`, matching inspect's own bash tool: a benchmark image
        # whose environment lives in /etc/profile.d (conda, rustup, nvm) must
        # give the probe the same PATH the evaluated agent had, or the auditor
        # concludes a present tool is missing
        result = await sandbox(service).exec(
            ["bash", "--login", "-c", cmd], timeout=180
        )
        # inspect's own bash-tool convention: stderr first, then stdout
        output = f"{result.stderr}\n" if result.stderr else ""
        return f"{output}{result.stdout}"

    return execute


@tool
def view_image() -> Tool:
    async def execute(path: str) -> ToolResult:
        """Look at an image: an item's media, or a figure you have drawn.

        Reading the bytes with bash establishes nothing; call this to see the picture.
        Auditing an item, the media is staged under `/audit/media/` and `sample.json`
        gives the path. Writing a report, this is how you check a figure before you
        publish it, from the file your script wrote under
        `/workspace/report/evidence/`.

        Args:
            path: The image's path in this box.
        """
        try:
            data = await sandbox().read_file(path, text=False)
        except Exception as ex:
            raise ToolError(
                f"Could not read {path!r}: {type(ex).__name__}: {ex}"
            ) from None
        if not isinstance(data, bytes):
            raise ToolError(f"{path!r} did not read back as bytes.")
        if Path(path).suffix.lower() == ".svg" or b"<svg" in data[:4096]:
            # Render inside the sandbox: SVGs can reference other files. Keep the
            # vector original for publication; only the model preview is rasterized.
            try:
                result = await sandbox().exec(
                    [
                        "python", "-c",
                        "import base64, subprocess, sys; "
                        "p = subprocess.run(['rsvg-convert', '--', sys.argv[1]], "
                        "capture_output=True, check=True, timeout=45); "
                        "print(base64.b64encode(p.stdout).decode())",
                        path,
                    ],
                    timeout=60,
                )
                if not result.success:
                    raise ValueError(result.stderr[-2000:])
                data = base64.b64decode(result.stdout.strip(), validate=True)
            except Exception as ex:
                raise ToolError(
                    f"Could not preview SVG {path!r}: {ex}. "
                    "Install librsvg2-bin in the sandbox or export a PNG and view that."
                ) from None
        try:
            with Image.open(BytesIO(data)) as preview:
                image_format = preview.format
                preview.verify()
            mime = {"PNG": "image/png", "JPEG": "image/jpeg",
                    "GIF": "image/gif", "WEBP": "image/webp"}.get(image_format or "")
            if mime is None:
                raise ValueError(f"unsupported image format {image_format}; export a PNG")
        except Exception as ex:
            raise ToolError(f"Cannot preview {path!r}: {ex}") from None
        encoded = base64.b64encode(data).decode()
        return [ContentImage(image=f"data:{mime};base64,{encoded}")]

    return execute


@tool(name="grade")
def grade_benchmark(scorers: list[Scorer]) -> Tool:
    async def execute(answer: str) -> str:
        """Grade a submission with the benchmark's own grader.

        Runs the real scorer against the benchmark's own state of the world:
        the item's question and choices, the session built with `attempt`
        (empty if you built none), the benchmark box as it currently stands,
        and `answer` as the submission. Returns each scorer's grade and
        explanation, stamped with the session's provenance mix -- how
        synthetic the graded evidence was is part of the result.

        Args:
            answer: Submission to grade as the attempt's completion. Pass an
                empty string when the submission is the state of the box
                (apply it with `benchmark_bash` first) rather than a text
                answer.
        """
        if not scorers:
            raise ToolError("This benchmark exposes no grader to grade with.")
        state = sample_state()
        if state is None:
            raise ToolError("Grading is only available while auditing a sample.")

        # the grader judges the benchmark's own TaskState, never the audit's:
        # its question, its choices, its metadata, the reconstructed session
        session = store_as(BenchmarkState)
        graded = benchmark_task_state(state, session, answer)

        # the benchmark's scorer calls sandbox() expecting the eval's own box; in the
        # auditor's two-box world that default is us, so aim it at the benchmark.
        # only when a benchmark box exists: redirecting to an absent name would
        # resolve BACK to the auditor on a one-environment sample.
        redirect = (
            sandbox_default(BENCHMARK_SERVICE) if has_benchmark_box() else nullcontext()
        )
        results: list[dict[str, Any]] = []
        with redirect:
            for scorer in scorers:
                try:
                    score = await scorer(graded, graded.target)
                except LimitExceededError:
                    raise
                except Exception as ex:
                    # a grader that cannot run (its judge model is gone, its
                    # sandbox call failed) is a fact for the auditor to record,
                    # not a reason to error the sample and cancel the run
                    # Provider exceptions may follow a long request dump. Prefer
                    # the underlying exception so the diagnostic reaches the agent.
                    cause: BaseException = ex
                    visited = {id(cause)}
                    while (
                        cause.__cause__ is not None
                        and id(cause.__cause__) not in visited
                    ):
                        cause = cause.__cause__
                        visited.add(id(cause))
                    raise ToolError(
                        f"the benchmark's grader failed to run: {type(cause).__name__}: "
                        f"{str(cause)}"
                    ) from ex
                if score is not None:
                    results.append(
                        {
                            "value": score.value,
                            "answer": score.answer,
                            "explanation": score.explanation,
                        }
                    )
        return json.dumps(
            {
                "scores": results if len(results) != 1 else results[0],
                "graded": {
                    "session": session.seeded,
                    "provenance": session.provenance_mix(),
                    "box_version": session.box_version,
                    "box_method": session.box_method,
                },
            }
        )

    return execute


@tool(name="reset")
def reset_benchmark() -> Tool:
    async def execute(hard: bool) -> str:
        """Restore the benchmark environment to its pristine per-sample state.

        Pass `hard=false` for the cheap path between graded attempts: revert the
        box in place -- return every repo to HEAD and re-run the sample's setup.
        Pass `hard=true` to instead rebuild the container from its image: the
        only reset that recovers a *bricked* box (a killed process, a corrupted
        or filled filesystem, a hang). You do not have to guess right -- a soft
        reset that fails on a damaged box rebuilds on its own -- so reach for
        `hard=true` when you have deliberately broken the box and know it needs one.

        Args:
            hard: Rebuild the container from its image (true) rather than
                reverting it in place (false).
        """
        state = sample_state()
        if state is None:
            raise ToolError("Reset is only available while auditing a sample.")
        if not has_benchmark_box():
            # without this check, `sandbox("benchmark")` on a one-environment
            # sample resolves to the AUDITOR's box, and the reset would git-wipe
            # the audit cell itself while reporting success
            raise ToolError("This item has no benchmark environment to reset.")
        metadata = state.metadata or {}
        script = metadata.get("benchmark_setup")
        files = metadata.get("benchmark_files")
        session = store_as(BenchmarkState)

        if hard:
            summary = await _phoenix(script, files)
            method = "phoenix"
        else:
            try:
                reset_repos = await restore_benchmark(script)
                # name what was actually reset, so a repo-backed benchmark whose
                # worktree was never found reads as "reset nothing" rather than a
                # silent success -- the auditor can see it and reach for hard
                if reset_repos:
                    summary = (
                        f"benchmark reset in place (repos: {', '.join(reset_repos)})"
                    )
                else:
                    summary = (
                        "benchmark reset in place; no git worktree was reset "
                        "(setup-only state, or none found)"
                    )
                method = "soft"
            except Exception as ex:
                # the soft revert runs commands *inside* the box; if it failed the
                # box may be bricked, so rebuild rather than leave the auditor stuck
                summary = (
                    f"{await _phoenix(script, files)} (soft reset failed first: "
                    f"{type(ex).__name__})"
                )
                method = "phoenix"

        # the bump is what lets a grade receipt say which box state it judged
        session.box_version = session.box_version + 1
        session.box_method = method
        return f"{summary} (box_version {session.box_version})"

    return execute


async def _phoenix(script: str | None, files: dict[str, str] | None) -> str:
    # a phoenix that cannot revive the box is not a tool crash but a finding the
    # auditor should see and record, so surface it as a ToolError not a raw raise.
    # TimeoutError as well as RuntimeError: a degraded-daemon brick that hangs the
    # rebuild is exactly such a finding, not an uncaught crash.
    try:
        return await phoenix_benchmark(script, files)
    except (RuntimeError, TimeoutError) as ex:
        raise ToolError(str(ex)) from None


@tool
def submit_audit(items: list[AuditItemSkill]) -> Tool:
    async def execute(
        environment_issues: list[Evidence],
        unresolved: list[Evidence],
        improvements: list[Evidence],
    ) -> str:
        """Submit your audit, once every item has a recorded verdict.

        Args:
            environment_issues: Audit setup or tool failures, with tool-event sources.
            unresolved: Blocked or unreviewed checks and their evidence references.
            improvements: Suggested changes with supporting observations; distinguish
                audit repairs from benchmark changes and state whether the intended
                task is preserved. These are proposals, not benchmark findings.
        """
        recorded = store_as(Verdicts).verdicts
        missing = [item.name for item in items if item.name not in recorded]
        if missing:
            raise ToolError(f"No verdict recorded for: {', '.join(missing)}.")
        store_as(Verdicts).debrief = {
            "environment_issues": environment_issues or [],
            "unresolved": unresolved or [],
            "improvements": improvements or [],
        }
        return json.dumps({item: verdict.grade for item, verdict in recorded.items()})

    return execute


def auditor_tools(
    scoped: list[AuditItemSkill],
    *,
    benchmark_scorers: Scorer | list[Scorer] | None = None,
    media: bool = False,
    contract: SolverContract | None = None,
    benchmark: bool = False,
) -> list[Tool]:
    """The auditor's tools for one run, in two namespaces.

    `audit_*` are the auditor's own instruments; `benchmark_*` mirror the
    evaluated agent's tools. `grade` is granted whenever the benchmark has a
    scorer; the mutating tools (`attempt`, `reset`) stay gated on a scoped
    item's skill asking for them, so a passive item keeps the box and the
    session observe-only.

    Args:
        scoped: The audit items in scope for this run.
        benchmark_scorers: The audited task's scorer(s), for `grade`.
        media: Whether to grant the image tools.
        contract: The audited task's declared surface, for mirrored tools and
            the reconstructed prompt.
        benchmark: Whether a benchmark environment exists to mirror tools into.
    """
    # only the skills in scope (plus the support skills), or a two-item audit
    # still pays for all thirteen descriptions every turn and can invoke bodies
    # it may never record a verdict for
    granted_skills = {item.name for item in scoped} | set(SUPPORT_SKILLS)
    skills = [
        str(path)
        for path in sorted(SKILLS.iterdir())
        if path.is_dir() and path.name in granted_skills
    ]

    # two namespaces, kept distinct so provenance stays unambiguous:
    #   audit_*     -- your own instruments (your box, an off-record probe of theirs)
    #   benchmark_* -- the evaluated agent's own tools, mirrored, recorded when enacted
    tools: list[Tool] = [
        ToolDef(bash(timeout=180), name="audit_bash").as_tool(),
        skill(skills),
        record_verdict(scoped),
    ]

    # the probe only exists where a benchmark box does: on a box-less sample,
    # inspect resolves a named lookup to the DEFAULT environment, so a probe
    # "into the benchmark" would silently hand back the auditor's own box
    if benchmark:
        tools.append(audit_probe())

    # a vision item needs to be looked at, and measured; both are useless elsewhere
    if media:
        tools += [view_image(), python(timeout=180)]

    # the evaluated agent's own tools, mirrored: enacting one runs it for real in
    # the benchmark box and records the call into the attempt. only with a box to
    # run them in -- and only the tools we could rebuild from the registry
    if benchmark and contract is not None and contract.tools:
        tools += benchmark_tools(contract.tools, AUDIT_ROOT)

    scorer_list = (
        benchmark_scorers
        if isinstance(benchmark_scorers, list)
        else [benchmark_scorers]
        if benchmark_scorers is not None
        else []
    )
    if scorer_list:
        tools.append(grade_benchmark(scorer_list))

    granted = {name for item in scoped for name in item.tools}
    if "attempt" in granted:
        tools.append(attempt(AUDIT_ROOT, prompt=contract.prompt if contract else None))
    if "reset" in granted:
        tools.append(reset_benchmark())

    return tools


@agent
def audit_agent(
    items: list[str] | None = None,
    model: str | None = None,
    benchmark_scorers: Scorer | list[Scorer] | None = None,
    media: bool = False,
    reasoning_effort: str | None = None,
    notes: str | None = None,
    confidential: bool = False,
    contract: SolverContract | None = None,
    benchmark: bool = False,
) -> Agent:
    """An auditor: a react loop with the audit skills and a shell in the item's sandbox.

    Args:
        items: Audit items to investigate (defaults to all of them).
        model: Model to audit with (defaults to the evaluated model).
        benchmark_scorers: The audited task's own scorer(s), for the `grade` tool.
        media: Whether the audited items carry images. Grants `view_image` and
            `python`, without which a vision benchmark is audited blind: its media
            reaches the cell as a path, and a PNG read with `bash` establishes
            nothing about what is in the picture.
        reasoning_effort: Reasoning effort for the auditor model, when it takes one.
        notes: A free-form steer inserted into the system prompt -- what the operator
            has been thinking about (a suspected route, a specific hint). Kept out of
            the skills so a skill stays general and the steer stays a per-run knob.
        confidential: The benchmark is unpublished. Instructs the auditor not to
            transmit item content off the box -- it keeps its shell and its internet,
            but must not paste the item into a search query or any other request.
        contract: The audited task's declared interaction surface. Its tools are
            mirrored as `benchmark_*` for the auditor to enact against the box,
            and its prompt seeds `attempt(new)`.
        benchmark: Whether a benchmark environment exists to mirror tools into.
    """
    # resolve the model object here so a generate config binds to it -- react
    # re-resolves a bare string without one, so the config would be dropped
    resolved: str | Model | None = model
    if reasoning_effort is not None:
        if model is None:
            # there is nothing to bind the config to yet (the eval's model is
            # not resolved at task-construction time), and dropping the knob
            # silently corrupts effort comparisons
            raise ValueError(
                "reasoning_effort needs an explicit model to bind to: pass "
                "model= alongside it (the eval-level --model cannot carry it)"
            )
        # a str arg so the CLI can pass it; GenerateConfig validates the value
        resolved = get_model(
            model, config=GenerateConfig(reasoning_effort=cast(Any, reasoning_effort))
        )
    # name the items under investigation in the system message: an auditor that is
    # not told what it is looking for picks whichever skill looks most relevant
    scoped = audit_items(items)
    named = "\n".join(f"- `{item.name}`: {item.description}" for item in scoped)

    tools = auditor_tools(
        scoped,
        benchmark_scorers=benchmark_scorers,
        media=media,
        contract=contract,
        benchmark=benchmark,
    )

    return react(
        name="auditor",
        description="Audits one benchmark item and submits a verdict per audit item.",
        prompt=prompts.AUDIT.format(
            root=AUDIT_ROOT,
            items=named,
            confidential=(
                prompts.AUDIT_CONFIDENTIAL.format(root=AUDIT_ROOT) if confidential else ""
            ),
            notes=prompts.AUDIT_NOTES.format(notes=notes) if notes else "",
        ),
        tools=tools,
        model=resolved,
        submit=AgentSubmit(
            tool=submit_audit(scoped), name="submit", keep_in_messages=True
        ),
    )


def item_scorer(item: AuditItemSkill) -> Scorer:
    """A scorer surfacing the auditor's verdict on one audit item."""
    # inspect's own categorical metric, with the skill's grades declared so a
    # grade nobody earned still reports as 0.0 and the metric round-trips
    # through recompute_metrics(). a dynamic registry name gives each item its
    # own score column; the cost is that a cold `inspect score` cannot resolve
    # these names to re-score a log
    distribution = frequency(categories=[*item.grades, "NO_VERDICT"])

    @scorer(metrics=[distribution], name=item.name)
    def factory() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            verdict = state.store_as(Verdicts).verdicts.get(item.name)
            if verdict is None:
                return Score(value="NO_VERDICT")
            return Score(
                value=verdict.grade,
                answer=verdict.grade,
                explanation="\n\n".join(
                    f"{e.observed}\n  -- {e.source}" for e in verdict.evidence
                )
                or None,
                metadata={
                    "evidence": [e.model_dump() for e in verdict.evidence],
                    "approaches": verdict.approaches,
                    **verdict.details,
                    "tried": verdict.tried,
                    "remarks": verdict.remarks,
                    "debrief": {
                        key: [e.model_dump() for e in entries]
                        for key, entries in state.store_as(Verdicts).debrief.items()
                    },
                },
            )

        return score

    return factory()
