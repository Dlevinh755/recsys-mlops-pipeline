"""Figures logged next to every AGF run (`mlflow.log_figure`, Artifacts tab).

Pure matplotlib (Agg backend, no display) — `matplotlib` already comes with
`mlflow`, so no new dependency. Both functions take plain Python data and
return a `Figure`; the caller logs and closes it.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
from matplotlib.figure import Figure  # noqa: E402

AGF_COLOR = "#1f77b4"
POP_COLOR = "#7f7f7f"
METRIC_ORDER = ("recall@10", "ndcg@10", "recall@20", "ndcg@20")


def training_curves_figure(
    history: list[dict], best_epoch: int, baseline_val: dict[str, float]
) -> Figure:
    """3 panels over epochs: train loss, valid NDCG@10, valid Recall@10. The
    two valid panels carry the popularity baseline as a dashed horizontal
    line, so "is AGF actually better than Pop" reads off one chart."""
    epochs = [row["epoch"] for row in history]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))

    axes[0].plot(epochs, [row["loss"] for row in history], color=AGF_COLOR)
    axes[0].set_title("Train loss (tổng theo epoch)")
    axes[0].set_ylabel("loss")

    for ax, key, title in (
        (axes[1], "ndcg@10", "Valid NDCG@10"),
        (axes[2], "recall@10", "Valid Recall@10"),
    ):
        ax.plot(epochs, [row[f"valid_{key}"] for row in history], color=AGF_COLOR, label="AGF-GRU4Rec")
        ax.axhline(baseline_val[key], color=POP_COLOR, linestyle="--", label="Popularity")
        ax.set_title(title)
        ax.legend()

    for ax in axes:
        ax.axvline(best_epoch, color="tab:green", linestyle=":", alpha=0.8)
        ax.set_xlabel(f"epoch (đường xanh lá = best epoch {best_epoch})")
        ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig


def baseline_comparison_figure(
    val: dict[str, float], test: dict[str, float],
    baseline_val: dict[str, float], baseline_test: dict[str, float],
) -> Figure:
    """Grouped bars AGF vs Popularity for Recall/NDCG @10/@20 on valid and test."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
    width = 0.38
    for ax, split, agf, pop in (
        (axes[0], "Valid", val, baseline_val),
        (axes[1], "Test", test, baseline_test),
    ):
        xs = range(len(METRIC_ORDER))
        agf_vals = [agf[m] for m in METRIC_ORDER]
        pop_vals = [pop[m] for m in METRIC_ORDER]
        bars_agf = ax.bar([x - width / 2 for x in xs], agf_vals, width, color=AGF_COLOR, label="AGF-GRU4Rec")
        bars_pop = ax.bar([x + width / 2 for x in xs], pop_vals, width, color=POP_COLOR, label="Popularity")
        ax.bar_label(bars_agf, fmt="%.3f", fontsize=8)
        ax.bar_label(bars_pop, fmt="%.3f", fontsize=8)
        ax.set_xticks(list(xs), METRIC_ORDER)
        ax.set_title(f"{split}: AGF so với Popularity")
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("điểm")
    axes[0].legend()
    fig.tight_layout()
    return fig
