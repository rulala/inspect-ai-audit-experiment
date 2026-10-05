"""Figure and table helpers with a fixed, restrained style.

Call from a retained analysis script in the report directory. Titles belong in the
Quarto document, one short line each; captions carry the denominator.
"""

import csv
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

COLOURS = ["#426b8c", "#d97757", "#5b9a6b", "#8c6b9e", "#c9a227", "#6b7c8c"]


def _axes(figsize: tuple[float, float] = (7, 4)) -> "tuple[Figure, Axes]":
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "text.color": "#20252b",
                         "axes.labelcolor": "#20252b", "axes.edgecolor": "#687b8b"})
    fig, ax = plt.subplots(figsize=figsize, layout="constrained")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, alpha=0.15)
    return fig, ax


def _save(fig: "Figure", output: str) -> None:
    import matplotlib.pyplot as plt

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def bar_chart(labels: Sequence[str], values: Sequence[float], output: str, *, ylabel: str) -> None:
    """One bar per label."""
    if not labels or len(labels) != len(values):
        raise ValueError("Supply one value per label, with at least one bar")
    _data(output, "bar_chart", dict(labels=list(labels), values=list(values), ylabel=ylabel))
    fig, ax = _axes()
    ax.bar(list(labels), list(values), color=COLOURS[0])
    ax.set_ylabel(ylabel)
    _save(fig, output)


def stacked_bars(
    labels: Sequence[str], parts: dict[str, Sequence[float]], output: str, *, ylabel: str
) -> None:
    """One bar per label, stacked by the named parts (e.g. outcome categories per model)."""
    if not labels or not parts or any(len(v) != len(labels) for v in parts.values()):
        raise ValueError("Every part needs one value per label")
    fig, ax = _axes()
    _data(output, "stacked_bars", dict(labels=list(labels), parts={k: list(v) for k, v in parts.items()}, ylabel=ylabel))
    bottom = [0.0] * len(labels)
    for colour, (name, values) in zip(COLOURS * 4, parts.items(), strict=False):
        ax.bar(list(labels), list(values), bottom=bottom, label=name, color=OUTCOMES.get(name.lower(), colour))
        bottom = [b + v for b, v in zip(bottom, values, strict=True)]
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False)
    _save(fig, output)


def line_chart(
    x: Sequence[float], series: dict[str, Sequence[float]], output: str, *, xlabel: str, ylabel: str, logx: bool = False
) -> None:
    """Lines over a shared x (e.g. accuracy against tokens or attempts), labelled at the end."""
    if not x or not series or any(len(v) != len(x) for v in series.values()):
        raise ValueError("Every series needs one value per x")
    fig, ax = _axes()
    _data(output, "line_chart", dict(x=list(x), series={k: list(v) for k, v in series.items()}, xlabel=xlabel, ylabel=ylabel, logx=logx))
    for colour, (name, values) in zip(COLOURS * 4, series.items(), strict=False):
        ax.plot(list(x), list(values), marker="o", ms=3, lw=1.5, color=colour)
        ax.annotate(name, (x[-1], values[-1]), xytext=(4, 0), textcoords="offset points", fontsize=8, va="center")
    if logx:
        ax.set_xscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    _save(fig, output)


def table_from_csv(path: str, *, columns: Sequence[str] | None = None, limit: int = 50) -> str:
    """Return a Markdown table from a CSV file, for inclusion in the Quarto document."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return "_(empty table)_"
    cols = list(columns or rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in rows[:limit]:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    if len(rows) > limit:
        lines.append(f"| … {len(rows) - limit} more rows |" + " |" * (len(cols) - 1))
    return "\n".join(lines)


def transcript(speaker: str, text: str, source: str) -> str:
    """Escaped HTML for a Quarto raw HTML block, with a source locator."""
    return (
        f'<div class="transcript"><strong>{escape(speaker)}</strong>'
        f'<pre>{escape(text)}</pre><div class="source">{escape(source)}</div></div>'
    )


OUTCOMES = {
    "correct": "#39785b", "incorrect": "#b75b4b", "abstained": "#9276aa",
    "errored": "#bd841f", "limited": "#687b8b", "missing": "#d9dfe5",
}


def _data(output: str, kind: str, data: object) -> None:
    """Retain exact plotting inputs beside the figure."""
    import json

    path = Path(output).with_suffix(".data.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"component": kind, "data": data}, indent=2, allow_nan=False))


def outcome_bars(counts: dict[str, int], output: str) -> None:
    """Named outcome counts; do not merge errors or abstentions into incorrect."""
    if not counts or any(k not in OUTCOMES or v < 0 for k, v in counts.items()):
        raise ValueError(f"Use nonnegative counts and categories {list(OUTCOMES)}")
    _data(output, "outcome_bars", counts)
    fig, ax = _axes()
    ax.bar(list(counts), list(counts.values()), color=[OUTCOMES[k] for k in counts])
    ax.set_ylabel("Attempts")
    _save(fig, output)


def paired_plot(labels: Sequence[str], before: Sequence[float], after: Sequence[float],
                output: str, *, conditions: tuple[str, str], ylabel: str) -> None:
    """Each line links the same item/configuration under two conditions."""
    if not labels or not len(labels) == len(before) == len(after):
        raise ValueError("Each matched pair needs a label and two observations")
    _data(output, "paired_plot", dict(labels=list(labels), before=list(before), after=list(after),
                                      conditions=conditions, ylabel=ylabel))
    fig, ax = _axes()
    for i, (a, b) in enumerate(zip(before, after, strict=True)):
        ax.plot([0, 1], [a, b], "o-", color=COLOURS[i % len(COLOURS)], alpha=0.7,
                label=labels[i] if len(labels) <= 8 else None)
    ax.set_xticks([0, 1], conditions)
    ax.set_xlim(-0.2, 1.2)
    ax.set_ylabel(ylabel)
    if len(labels) <= 8:
        ax.legend(frameon=False, fontsize=8)
    _save(fig, output)


def response_matrix(csv_path: str, output: str) -> None:
    """CSV rows=model configurations, first column=id, other columns=item IDs; 0/1/blank."""
    import numpy as np
    import pandas as pd
    from matplotlib.colors import ListedColormap

    frame = pd.read_csv(csv_path, index_col=0)
    values = frame.to_numpy(dtype=float)
    if not np.all(np.isnan(values) | (values == 0) | (values == 1)):
        raise ValueError("Responses must be 0, 1, or missing")
    _data(output, "response_matrix", dict(source=csv_path, rows=list(map(str, frame.index)),
        columns=list(map(str, frame.columns)), values=frame.astype(object).where(frame.notna(), None).values.tolist()))
    fig, ax = _axes((9, max(3, min(12, len(frame) * 0.24))))
    cmap = ListedColormap([OUTCOMES["incorrect"], OUTCOMES["correct"]])
    cmap.set_bad(OUTCOMES["missing"])
    ax.imshow(np.ma.masked_invalid(values), aspect="auto", cmap=cmap, vmin=0, vmax=1,
              interpolation="nearest")
    ax.grid(False)
    ax.set_xlabel("Items (column order in source CSV)")
    ax.set_ylabel("Model configurations")
    if len(frame) <= 40:
        ax.set_yticks(range(len(frame)), list(map(str, frame.index)), fontsize=7)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=OUTCOMES[k], label=k) for k in ["incorrect", "correct", "missing"]],
              loc="upper center", bbox_to_anchor=(0.5, 1.15), ncol=3, frameon=False)
    _save(fig, output)


def architecture(spec: dict[str, Any], output: str) -> None:
    """Render an agent-boundary diagram using Graphviz, with retained source JSON.

    spec={"nodes":[{"id":"agent","label":"Model","visible":True,"kind":"agent"}],
          "edges":[{"from":"task","to":"agent","label":"Question",
                    "source":"task.py:12","verified":True}]}
    Node kinds: dataset, agent, tools, environment, scorer, aggregate. Hidden inputs
    have visible=False. Every edge needs a source locator (or verified=False).
    """
    import json
    import subprocess

    nodes, edges = spec["nodes"], spec["edges"]
    ids = [n["id"] for n in nodes]
    if not ids or any(not isinstance(i, str) or not i.strip() for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("Node IDs must be nonempty and unique")
    for edge in edges:
        if edge["from"] not in ids or edge["to"] not in ids:
            raise ValueError("Every edge must reference declared nodes")
        if edge.get("verified", True) and not edge.get("source"):
            raise ValueError("Verified edges require source locators")
    if Path(output).suffix != ".svg":
        raise ValueError("Architecture output must be .svg")
    _data(output, "architecture", spec)
    def q(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)
    lines = ['digraph benchmark {', 'rankdir=LR; bgcolor="white"; pad=0.25; fontname="sans-serif"; fontcolor="#20252b";',
             'node [shape=box, style="rounded,filled", fillcolor="#f3f5f7", color="#687b8b", fontname="sans-serif", fontsize=11, margin=0.15];',
             'edge [color="#687b8b", fontname="sans-serif", fontsize=9];']
    def node(n: dict[str, Any]) -> str:
        shape = "cylinder" if n.get("kind") == "dataset" else "box"
        return f'{q(n["id"])} [label={q(n["label"])}, shape={shape}];'
    lines += ['subgraph cluster_visible { label="Agent-visible"; style="rounded,filled"; color="#d2e1ec"; fillcolor="#edf4f9";']
    lines += [node(n) for n in nodes if n.get("visible", False)]
    lines += ['}'] + [node(n) for n in nodes if not n.get("visible", False)]
    for e in edges:
        label = e["label"] + (" (unverified)" if not e.get("verified", True) else "")
        style = "solid" if e.get("verified", True) else "dashed"
        lines.append(f'{q(e["from"])} -> {q(e["to"])} [label={q(label)}, style={style}];')
    lines.append('}')
    dot = Path(output).with_suffix(".dot")
    dot.write_text("\n".join(lines))
    subprocess.run(["dot", "-Tsvg", str(dot), "-o", output], check=True, timeout=30)
