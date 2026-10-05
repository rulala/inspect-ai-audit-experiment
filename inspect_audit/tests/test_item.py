"""The filesystem an auditor gets for one item.

Uses a real `Task` and a real scorer rather than fixtures that resolve a published
benchmark, so the tests need no network and run in milliseconds.
"""

import json
from pathlib import Path

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample, json_dataset
from inspect_ai.scorer import match

from inspect_audit import AuditItem
from inspect_audit._item import AUDIT_ROOT, item_files, item_sample

QUESTION = "In what year did the Battle of Hastings take place?"


def make_task(metadata: dict[str, object] | None = None) -> Task:
    return Task(
        name="fixture_task",
        dataset=MemoryDataset([Sample(id=42, input=QUESTION, target="1066", metadata=metadata)]),
        scorer=match(),
    )


def test_every_value_is_a_host_path_never_contents(tmp_path: Path) -> None:
    """Contents-shaped values are ambiguous to Inspect; paths are not.

    Inspect resolves a `Sample.files` value as a data URI, then an HTTP GET, then an
    existing file at that path. A target of "pyproject.toml" would therefore be
    replaced by that file's bytes, and a URL-shaped target fetched from the web.
    """
    task = make_task()
    files = item_files(task, task.dataset[0], [], stage=tmp_path)

    assert files
    for value in files.values():
        assert Path(value).is_file(), f"{value} is not a staged file"


def test_sample_is_written_in_inspects_own_shape(tmp_path: Path) -> None:
    """The sample is loadable as a dataset, not a rendering of ours."""
    task = make_task({"references": ["https://example.org/a"]})
    files = item_files(task, task.dataset[0], [], stage=tmp_path)
    path = files[f"{AUDIT_ROOT}/sample.json"]

    assert json.loads(Path(path).read_text()) == [
        {
            "id": 42,
            "input": QUESTION,
            "target": "1066",
            "metadata": {"references": ["https://example.org/a"]},
        }
    ]

    loaded = json_dataset(path)
    assert loaded[0].input == QUESTION
    assert loaded[0].target == "1066"
    assert (loaded[0].metadata or {})["references"] == ["https://example.org/a"]


def test_grading_doc_points_at_the_real_artefacts_rather_than_restating_them(
    tmp_path: Path,
) -> None:
    task = make_task({"note": "see https://b.example"})
    files = item_files(task, task.dataset[0], [], stage=tmp_path)
    grading = Path(files[f"{AUDIT_ROOT}/gold/grading.md"]).read_text()

    # Where the code lives: a task's scorer routinely delegates outside its own package.
    assert "inspect_ai.scorer" in grading
    assert "importlib" in grading
    # How to read the logs, through Inspect's API rather than a schema of ours.
    assert "read_eval_log" in grading
    assert "samples_df" in grading
    # Metadata keys are surfaced so an auditor can spot reference material we cannot name.
    assert "`note`" in grading
    # And the caveat that `target` may not be the whole gold.
    assert "whole gold" in grading


def test_grading_doc_import_line_handles_multiple_scorer_modules(tmp_path: Path) -> None:
    """The module-source snippet must stay runnable when scorers span two packages.

    A task whose scorers come from different modules space-joins them into the
    `{modules}` slot; the old snippet fed that straight to a single
    `import_module(...)`, which is broken Python. It must iterate instead.
    """
    from inspect_ai.scorer import Score, Scorer, Target, accuracy, match, scorer
    from inspect_ai.solver import TaskState

    # a scorer defined here, so its module differs from match()'s -- the case the
    # bug hit: two scorer packages space-joined into one import statement
    @scorer(metrics=[accuracy()])
    def local_scorer() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            return Score(value="C")

        return score

    task = Task(
        name="two_scorer_task",
        dataset=MemoryDataset([Sample(id=1, input=QUESTION, target="1066")]),
        scorer=[match(), local_scorer()],
    )
    files = item_files(task, task.dataset[0], [], stage=tmp_path)
    grading = Path(files[f"{AUDIT_ROOT}/gold/grading.md"]).read_text()

    # the doc names both distinct modules
    assert "inspect_ai.scorer" in grading
    assert __name__ in grading

    line = next(ln for ln in grading.splitlines() if "getsourcefile" in ln)
    # the snippet splits the module list and imports each, rather than importing
    # one module named "mod_a mod_b"
    assert ".split()" in line
    body = line.split('python -c "', 1)[1].rsplit('"', 1)[0]
    compile(body, "<grading-snippet>", "exec")  # would raise if it were broken Python


def test_item_sample_records_which_item_it_audits(tmp_path: Path) -> None:
    task = make_task()
    audited = item_sample(
        task,
        task.dataset[0],
        AuditItem(task="fixture_task", sample_id=42),
        prompt="audit it",
        stage=tmp_path,
        sandbox="docker",
    )

    assert audited.id == "42"
    assert audited.input == "audit it"
    assert audited.target == "1066"
    assert (audited.metadata or {})["audit_item"]["sample_id"] == 42
    assert audited.files is not None and f"{AUDIT_ROOT}/sample.json" in audited.files


def test_redact_strips_extra_metadata_keys_and_their_names(tmp_path: Path) -> None:
    """A key that pre-empts the finding under audit is withheld, name included.

    Some benchmarks record the construction-time validator votes that *are* the
    label under audit; an auditor that reads them is no longer an independent
    witness. Naming the key alone leaks the finding, so `grading.md` must not
    list it either.
    """
    task = make_task({"answer": "42", "validator_votes": "1/3", "kind": "geometry"})
    files = item_files(
        task, task.dataset[0], [], stage=tmp_path, redact=("validator_votes",)
    )

    staged = json.loads(Path(files[f"{AUDIT_ROOT}/sample.json"]).read_text())[0]
    assert "validator_votes" not in staged["metadata"]
    assert staged["metadata"]["kind"] == "geometry"

    grading = Path(files[f"{AUDIT_ROOT}/gold/grading.md"]).read_text()
    assert "validator_votes" not in grading
    assert "`kind`" in grading


def test_benchmark_metadata_is_carried_without_a_benchmark_container(tmp_path: Path) -> None:
    """`grade` reads the benchmark's own metadata, and a sandboxless task still has one.

    A text benchmark declares no sandbox, so `benchmark=False`, but its scorer still
    reads `metadata` for the recorded answer. Withholding it there left `grade` -- and
    so the whole red-teaming item -- broken on every task without a container.
    """
    task = make_task({"answer": "42"})
    item = AuditItem(task=task.name, sample_id=42)
    sample = item_sample(
        task, task.dataset[0], item, prompt="p", stage=tmp_path, benchmark=False
    )

    assert (sample.metadata or {})["benchmark_metadata"] == {"answer": "42"}


def test_redaction_does_not_reach_the_grader(tmp_path: Path) -> None:
    """Redaction blinds the auditor, never the scorer that has to grade against it."""
    task = make_task({"answer": "1066", "validator_votes": "1/3"})
    item = AuditItem(task=task.name, sample_id=42)
    sample = item_sample(
        task,
        task.dataset[0],
        item,
        prompt="p",
        stage=tmp_path,
        redact=("validator_votes", "answer"),
    )

    assert (sample.metadata or {})["benchmark_metadata"] == {
        "answer": "1066",
        "validator_votes": "1/3",
    }


def test_benchmark_code_is_staged_so_the_auditor_can_read_the_grader(tmp_path: Path) -> None:
    """`gold/grading.md` tells the auditor to read the real grading code.

    `task_requirements` only pins distributions, so a benchmark that is a loose
    repository rather than a published package installs nothing into the auditor's
    box and that instruction fails. Stage the scorer's own source instead.
    """
    task = make_task()
    files = item_files(task, task.dataset[0], [], stage=tmp_path)

    staged = {k for k in files if k.startswith(f"{AUDIT_ROOT}/benchmark/")}
    assert staged, "no benchmark source staged"
    # `match()` is scored by inspect_ai's own module, which is what this fixture uses
    assert any(Path(files[k]).read_text().strip() for k in staged)


def test_benchmark_staging_excludes_data_and_logs(tmp_path: Path) -> None:
    """A benchmark's data directory is routinely large enough to swamp the cell."""
    from inspect_audit._item import benchmark_source_files

    sources = benchmark_source_files(make_task())
    assert all(p.suffix == ".py" for p in sources.values())
    assert not any("/data/" in str(p) or "/logs/" in str(p) for p in sources.values())


def test_item_media_is_copied_into_the_cell_and_rewritten(tmp_path: Path) -> None:
    """An image referenced by host path is dead inside the auditor's container.

    Inspect resolves dataset media against the machine that built the dataset. Staged
    verbatim, a vision item asks the auditor to check a chair count against a path
    that does not exist, so the item is audited blind.
    """
    from inspect_ai.model import ChatMessageUser
    from inspect_ai.tool import ContentImage, ContentText

    png = tmp_path / "view_0.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    task = Task(
        name="fixture_vision",
        dataset=MemoryDataset(
            [
                Sample(
                    id=1,
                    input=[
                        ChatMessageUser(
                            content=[
                                ContentText(text="count the chairs"),
                                ContentImage(image=str(png)),
                            ]
                        )
                    ],
                    target="3",
                )
            ]
        ),
        scorer=match(),
    )
    files = item_files(task, task.dataset[0], [], stage=tmp_path / "stage")

    staged = [k for k in files if k.startswith(f"{AUDIT_ROOT}/media/")]
    assert len(staged) == 1
    assert Path(files[staged[0]]).read_bytes().startswith(b"\x89PNG")

    record = json.loads(Path(files[f"{AUDIT_ROOT}/sample.json"]).read_text())[0]
    refs = [
        c["image"]
        for m in record["input"]
        for c in m["content"]
        if isinstance(c, dict) and c.get("type") == "image"
    ]
    assert refs == staged, "sample.json still points at the host path"


def test_media_staging_leaves_uris_alone(tmp_path: Path) -> None:
    """A data: or http URI needs no staging and must not be mangled."""
    from inspect_audit._item import media_files

    record = {
        "input": [
            {"content": [{"type": "image", "image": "data:image/png;base64,AAAA"}]},
            {"content": [{"type": "image", "image": "https://example.org/a.png"}]},
        ]
    }
    assert media_files(record, stage=tmp_path) == {}
    assert record["input"][0]["content"][0]["image"].startswith("data:")
    assert record["input"][1]["content"][0]["image"].startswith("https://")


def test_sample_files_route_through_the_service_renames(tmp_path: Path) -> None:
    """Prefixed files follow the same renames the compose merge applies.

    Blanket-prefixing once produced `benchmark:victim:/flag` -- a file
    literally named `victim:/flag` written into the wrong box. Their default
    (here `web`) becomes `benchmark`; a sibling keeps its name; an unprefixed
    file targets their default.
    """
    import yaml
    from inspect_ai.util import SandboxEnvironmentSpec

    compose = tmp_path / "compose.yaml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "web": {"image": "i", "x-default": True},
                    "victim": {"image": "v"},
                }
            }
        )
    )
    task = Task(
        name="fixture_task",
        dataset=MemoryDataset(
            [
                Sample(
                    id=1,
                    input="q",
                    target="a",
                    files={
                        "victim:/flag.txt": "sibling state",
                        "/work/x.txt": "default-box state",
                        "web:/srv/app.py": "their-default state",
                    },
                )
            ]
        ),
        scorer=match(),
    )
    spec = SandboxEnvironmentSpec("docker", str(compose))
    sample = item_sample(
        task,
        task.dataset[0],
        AuditItem(task="fixture_task", sample_id=1),
        prompt="p",
        stage=tmp_path / "stage",
        sandbox=("docker", str(compose)),
        original_env=spec,
        benchmark=True,
    )

    carried = (sample.metadata or {})["benchmark_files"]
    assert set(carried) == {
        "victim:/flag.txt",  # sibling keeps its name
        "benchmark:/work/x.txt",  # unprefixed -> their default's new name
        "benchmark:/srv/app.py",  # their default's own name follows the rename
    }


def test_two_media_files_with_the_same_name_do_not_collide(tmp_path: Path) -> None:
    """Distinct images sharing parent dir + basename must stage separately.

    Collapsed, both references point at whichever staged last, and the auditor
    grades a vision item against the wrong picture with no error anywhere.
    """
    from inspect_audit._item import media_files

    first = tmp_path / "x" / "views"
    second = tmp_path / "y" / "views"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "0.png").write_bytes(b"picture-A")
    (second / "0.png").write_bytes(b"picture-B")

    record = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": str(first / "0.png")},
                    {"type": "image", "image": str(second / "0.png")},
                ],
            }
        ]
    }
    files = media_files(record, stage=tmp_path / "stage")

    refs = [c["image"] for c in record["input"][0]["content"]]
    assert refs[0] != refs[1]
    assert Path(files[refs[0]]).read_bytes() == b"picture-A"
    assert Path(files[refs[1]]).read_bytes() == b"picture-B"
