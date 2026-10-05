"""Real tasks and real logs for tests.

Produced by running actual evals under `mockllm` rather than checked in, so a fixture
cannot drift from what Inspect writes today.
"""

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import match

__all__ = ["fixture_task", "run_fixture_eval", "run_graded_eval"]


def fixture_task(name: str = "fixture_task") -> Task:
    """A three-sample task with a real scorer."""
    return Task(
        name=name,
        dataset=MemoryDataset([Sample(id=i, input=f"q{i}", target="ANSWER") for i in (1, 2, 3)]),
        scorer=match(),
    )


def run_fixture_eval(log_dir: str, *, name: str = "fixture_task", epochs: int = 1) -> str:
    """Run a real eval and return its log location."""
    return eval(
        fixture_task(name),
        model="mockllm/model",
        log_dir=log_dir,
        display="none",
        epochs=epochs,
    )[0].location


def run_graded_eval(log_dir: str) -> str:
    """A real eval whose recorded grades are MIXED (C, I, C).

    A fixture where every grade is identical cannot tell a faithful regrade
    from a broken one that agrees with anything; this one can.
    """
    from inspect_ai.model import ModelOutput, get_model

    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.from_content("mockllm/model", content)
            for content in ("ANSWER", "WRONG", "ANSWER")
        ],
    )
    return eval(
        fixture_task("graded_task"),
        model=model,
        log_dir=log_dir,
        display="none",
        max_samples=1,  # sequential, so outputs map to samples 1, 2, 3
    )[0].location
