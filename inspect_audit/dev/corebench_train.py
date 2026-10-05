"""The CORE-bench train split, reconstructed.

The published inspect_evals task only serves the test split; the Mohl et al. train
runs used a local modification loading core_train.json (public on HF). This rebuilds
it from inspect_evals' own components. Verify against their logs before trusting:
each transcript's first user message is the rendered prompt.
"""

import json
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset
from inspect_evals.core_bench.core_bench import default_solver
from inspect_evals.core_bench.dataset import (
    CAPSULE_TAR_PATH,
    CAPSULE_URL,
    CORE_BENCH_DATASET_LOCATION,
    CORE_BENCH_DATASET_REVISION,
    get_record_to_sample_by_difficulty,
)
from inspect_evals.core_bench.scorer import evaluate_task_questions
from inspect_evals.utils.huggingface import hf_hub_download


@task
def core_bench_train(difficulty: str = "medium", download: bool = True) -> Task:
    path = hf_hub_download(
        repo_id="siegelz/core-bench",
        filename="core_train.json",
        repo_type="dataset",
        local_dir=str(CORE_BENCH_DATASET_LOCATION),
        revision=CORE_BENCH_DATASET_REVISION,
    )
    with open(path) as f:
        records = json.load(f)
    if download:
        # upstream's downloader checksums only the test capsules; fetch train
        # tarballs directly and record their digests alongside
        import hashlib
        import subprocess

        digests = Path(CORE_BENCH_DATASET_LOCATION) / "train_checksums.txt"
        seen = digests.read_text() if digests.exists() else ""
        for r in records:
            cid = r["capsule_id"]
            tar = Path(CAPSULE_TAR_PATH.format(capsule_id=cid))
            if tar.exists() or (Path(CORE_BENCH_DATASET_LOCATION) / cid).exists():
                continue
            print(f"downloading {cid}...")
            # download to a .part and rename, so an interrupted fetch never
            # masquerades as a complete tarball
            part = tar.with_suffix(".part")
            subprocess.run(
                ["curl", "-fsSL", "--retry", "5", "--retry-all-errors", "-C", "-",
                 "-o", str(part), CAPSULE_URL.format(capsule_id=cid)],
                check=True,
            )
            part.rename(tar)
            if cid not in seen:
                sha = hashlib.sha256(tar.read_bytes()).hexdigest()
                with open(digests, "a") as f:
                    f.write(f"{cid} {sha}\n")
    to_sample = get_record_to_sample_by_difficulty(difficulty)
    return Task(
        name="core_bench_train",
        dataset=MemoryDataset([to_sample(r) for r in records]),
        solver=default_solver(None),
        scorer=evaluate_task_questions(),
    )
