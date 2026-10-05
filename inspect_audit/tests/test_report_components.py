"""Report plots preserve source data; IRT rejects unusable populations."""
import json
from pathlib import Path

import pytest


def test_architecture_validates_connections_and_retains_sources(tmp_path: Path) -> None:
    import shutil

    from inspect_audit.investigation.report.components import architecture

    if not shutil.which("dot"):
        pytest.skip("Graphviz required (installed in investigation image)")
    spec = {"nodes": [{"id": "task", "label": "Question", "kind": "dataset"},
                      {"id": "agent", "label": "Model", "visible": True}],
            "edges": [{"from": "task", "to": "agent", "label": "Question", "source": "task.py:1"}]}
    output = tmp_path / "architecture.svg"
    architecture(spec, str(output))
    from html import unescape

    assert "Agent-visible" in unescape(output.read_text())
    assert json.loads(output.with_suffix('.data.json').read_text())["data"] == spec
    spec["edges"][0]["to"] = "undeclared"
    with pytest.raises(ValueError, match="declared"):
        architecture(spec, str(output))


def test_irt_screens_and_fits_synthetic_population(tmp_path: Path) -> None:
    pytest.importorskip("girth")
    import numpy as np
    import pandas as pd

    from inspect_audit.investigation.report.irt_analysis import fit_irt

    rng = np.random.default_rng(42)
    ability = rng.normal(size=150)
    difficulty = np.linspace(-2, 2, 30)
    probabilities = 1 / (1 + np.exp(-(ability[:, None] - difficulty)))
    responses = pd.DataFrame(rng.binomial(1, probabilities))
    matrix, metadata = tmp_path / 'matrix.csv', tmp_path / 'metadata.csv'
    responses.to_csv(matrix)
    pd.DataFrame({'family': [f'f{i % 6}' for i in range(150)]}).to_csv(metadata)
    result = fit_irt(str(matrix), str(metadata), str(tmp_path/'fit'), comparability="synthetic Rasch population")
    assert result['status'] == 'exploratory'
    estimates = pd.read_csv(tmp_path/'fit/items.csv')
    assert np.corrcoef(estimates.difficulty, difficulty)[0, 1] > .9
    assert (tmp_path/'fit/residuals.csv').exists()
    responses.iloc[:3].to_csv(matrix)
    result = fit_irt(str(matrix), str(metadata), str(tmp_path/'small'), comparability="too small")
    assert result['status'] == 'not_fitted'
