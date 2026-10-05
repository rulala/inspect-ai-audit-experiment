"""Optional exploratory IRT using GIRTH (https://github.com/eribean/girth).

CSV: rows are model configurations, columns are item IDs, first column is config ID.
Values are binary 0/1; missing/errors stay blank. Metadata CSV uses the same first
column and a `family` column. Repeated epochs must be resolved before fitting.
Call fit_irt(matrix_csv, metadata_csv, output_dir, comparability="...", model="rasch").
Thresholds below are conservative screening heuristics, not guarantees of validity.
"""
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def fit_irt(matrix_csv: str, metadata_csv: str, output_dir: str, *,
            comparability: str, model: str = "rasch") -> dict[str, Any]:
    """Save input snapshots, coverage diagnostics, fit parameters and residuals.

    Caller must explain comparable task/scorer/budget conditions and independence
    limitations. Rasch fixes discrimination=1; 2PL estimates it and needs more data.
    No inferential confidence intervals are fabricated from this exploratory fit.
    """
    from girth import ability_eap, rasch_mml, tag_missing_data, twopl_mml

    if model not in {"rasch", "2pl"} or not comparability.strip():
        raise ValueError("Choose rasch/2pl and provide a comparability assessment")
    frame = pd.read_csv(matrix_csv, index_col=0)
    metadata = pd.read_csv(metadata_csv, index_col=0)
    if not frame.index.is_unique or not metadata.index.is_unique or not frame.columns.is_unique:
        raise ValueError("Model configuration and item IDs must be unique; do not treat epochs as models")
    metadata = metadata.reindex(frame.index)
    if "family" not in metadata or metadata.family.isna().any():
        raise ValueError("Provide family metadata for every configuration")
    values = frame.to_numpy(dtype=float)
    if not np.all(np.isnan(values) | (values == 0) | (values == 1)):
        raise ValueError("IRT input must be 0/1/missing; errors are not incorrect")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "responses.csv")
    metadata.to_csv(out / "models.csv")
    variable = frame.nunique() == 2
    enough = frame.notna().sum() >= 10
    selected = frame.loc[:, variable & enough]
    diagnostics: dict[str, Any] = {
        "model": model, "configurations": len(frame), "families": metadata.family.nunique(),
        "items": len(frame.columns), "fitted_items": len(selected.columns),
        "excluded_items": list(map(str, frame.columns[~(variable & enough)])),
        "missing_fraction": float(np.isnan(values).mean()), "comparability": comparability,
        "limitations": ["Configurations within a family are correlated, not independent respondents",
                        "Coverage thresholds do not establish unidimensionality or local independence",
                        "Residuals below are in-sample diagnostics, not held-out validation",
                        "No parameter confidence intervals estimated"],
    }
    reasons = []
    if len(frame) < (100 if model == "2pl" else 20):
        reasons.append("Too few configurations for the selected exploratory fit")
    if metadata.family.nunique() < 5:
        reasons.append("Fewer than five declared model families")
    if selected.shape[1] < 10 or (selected.notna().sum(axis=1) < 10).any():
        reasons.append("Insufficient overlapping variable items per configuration")
    diagnostics["status"] = "not_fitted" if reasons else "exploratory"
    diagnostics["reasons"] = reasons
    (out / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2))
    if reasons:
        return diagnostics
    data = tag_missing_data(selected.to_numpy(dtype=float).T, [0, 1]).astype(int)
    estimates = rasch_mml(data) if model == "rasch" else twopl_mml(data)
    difficulty = np.asarray(estimates["Difficulty"])
    discrimination = np.broadcast_to(np.asarray(estimates["Discrimination"]), difficulty.shape)
    ability = np.asarray(ability_eap(data, difficulty, discrimination))
    if not all(np.isfinite(x).all() for x in [difficulty, discrimination, ability]):
        raise ValueError("IRT returned nonfinite parameters; do not interpret this fit")
    pd.DataFrame({"item": selected.columns, "difficulty": difficulty,
                  "discrimination": discrimination}).to_csv(out / "items.csv", index=False)
    pd.DataFrame({"configuration": frame.index, "ability": ability}).to_csv(out / "abilities.csv", index=False)
    from scipy.special import expit

    predicted = expit(discrimination[:, None] * (ability[None, :] - difficulty[:, None])).T
    residuals = selected.to_numpy(dtype=float) - predicted
    pd.DataFrame({"item": selected.columns, "mean_residual": np.nanmean(residuals, axis=0),
                  "observations": selected.notna().sum().values}).to_csv(out / "residuals.csv", index=False)
    diagnostics["mean_squared_residual"] = float(np.nanmean(residuals ** 2))
    (out / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2))
    return diagnostics
