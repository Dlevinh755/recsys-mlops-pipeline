"""Phase 10 (ADR 0008) — train AGF-GRU4Rec, the main ranking model since
ADR 0008 (replaces the ID-only GRU4Rec of ADR 0004, which stays runnable by
hand in `train_sequence.py` as a comparison point).

The method — architecture, training loop, attribute rules, hyperparameters —
comes unchanged from the experiment repo (`libs/.../ranking/agf/`,
`agf_method_config.yaml`); this job only wires it to the pipeline:

1. Read `gold.item_features` (full catalog + attributes) and
   `gold.user_features` (≤ 10-item sequences) — Iceberg is the source of
   truth (CLAUDE.md #3). Mapping in `agf_inputs.py`.
2. Fit vocab / `[UNK]` / price buckets again, encode text with frozen
   MiniLM (`--no-text` skips it — CI smoke test, offline runs).
3. Train with `trainer.train` (leave-one-out: last item → test, second to
   last → valid, CE over every item, early stopping on valid NDCG@10).
4. Log to MLflow (experiment + registry `REGISTERED_MODEL_NAME`):
   params (incl. run facts: `best_epoch`, `epochs_run`, `n_params`, sizes),
   per-epoch curves (`train_loss`, `val_*_epoch`, flat Pop baseline),
   `val_*`/`test_*` metrics, the popularity baseline on the same split
   (`baseline_pop_*`), two figures under `figures/`, the pickled model, the
   precomputed item table `agf/item_table.npy` that serving scores with, and
   `item_vocab.json` (`product_id -> index`, same format as before).

ADR 0015: `--candidates gru4rec,sasrec,gru4rec_sum` (env `TRAIN_CANDIDATES`)
trains several AGF variants on the same split, one MLflow run each, picks one
on validation (`candidates.py`) and registers only that one. Default
`gru4rec` = the base config, a single run, exactly as before.

CPU by default (`bao-cao-ky-thuat.md` 7.6 — ~1.5k items, ~16k interactions).
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import mlflow
import matplotlib.pyplot as plt
import mlflow.pytorch
import numpy as np
from pyiceberg.table import Table

from jobs.training.agf_inputs import (
    CatalogItems,
    build_catalog_items,
    catalog_meta,
    interactions_from_sequences,
)
from jobs.training.baselines import popularity_metrics
from jobs.training.candidates import (
    CANDIDATE_OVERRIDES,
    Scored,
    ndcg_per_sample,
    parse_candidates,
    select_winner,
)
from jobs.training.champion_eval import Champion, evaluate_champion, load_production_champion
from jobs.training.training_figures import baseline_comparison_figure, training_curves_figure
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_item_features, gold_user_features, silver_interactions
from reco_mlops_libs.mlflow_utils.logging_helpers import log_iceberg_snapshots
from reco_mlops_libs.ranking.agf import trainer as T
from reco_mlops_libs.ranking.base import REGISTERED_MODEL_NAME

EXPERIMENT_NAME = REGISTERED_MODEL_NAME
CONFIG_PATH = Path(__file__).with_name("agf_method_config.yaml")
ITEM_COLUMNS = ("product_id", "title", "category", "category_path", "brand", "price")
USER_COLUMNS = ("user_id", "item_sequence", "event_time_sequence")
MAX_TABLE_DIFF = 1e-5  # experiment/docs/03: H precomputed must match direct scores


def _prefixed(prefix: str, metrics: dict[str, float]) -> dict[str, float]:
    """`{"ndcg@10": x}` → `{"<prefix>_ndcg_at_10": x}` (MLflow-safe names,
    same `val_ndcg_at_10` key promote.py / monitoring already read)."""
    return {f"{prefix}_{key.replace('@', '_at_')}": value for key, value in metrics.items()}


def table_row_count(table: Table) -> int:
    """Rows in the table's current snapshot: the snapshot summary when it has
    `total-records`, else a metadata-only scan count."""
    snapshot = table.current_snapshot()
    total = snapshot.summary.additional_properties.get("total-records") if snapshot and snapshot.summary else None
    return int(total) if total is not None else int(table.scan().count())


def to_item_inputs(items: CatalogItems) -> T.ItemInputs:
    return T.ItemInputs(items.tokens, items.categories, items.store, items.price, items.text,
                        items.train_counts, items.vocab, items.price_edges)


@dataclass
class TrainedCandidate:
    """One finished MLflow run of one candidate (ADR 0015)."""

    id: str
    out: dict  # what the job prints for this run: run_id, metrics, run facts
    val_ndcg_per_sample: np.ndarray
    n_params: int
    model_uri: str


def _train_candidate(
    candidate_id: str,
    cfg: T.MethodConfig,
    *,
    inter,
    catalog: CatalogItems,
    items: T.ItemInputs,
    n_items: int,
    n_user_rows: int,
    seed: int,
    device: str,
    lineage_tables: dict[str, Table] | None,
    champion: Champion | None,
    group_id: str,
    run_name: str | None,
) -> TrainedCandidate:
    """Train one candidate and log everything to its own MLflow run. Does **not**
    register anything — only the selected winner is registered by `run_training`."""
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tag("model_type", f"agf-{cfg.backbone}")
        mlflow.set_tag("adr", "0008")
        mlflow.set_tag("candidate_id", candidate_id)
        mlflow.set_tag("candidate_group", group_id)
        mlflow.log_params({
            **{f"cfg_{k}": v for k, v in asdict(cfg).items()},
            "seed": seed,
            "n_items": n_items,
            "n_users_rows": n_user_rows,
            "n_interactions": len(inter),
            "items_with_train_count": int((catalog.train_counts[1:] > 0).sum()),
            "vocab_categories": len(catalog.vocab["categories"]),
            "vocab_store": len(catalog.vocab["store"]),
            "vocab_price_bucket": len(catalog.vocab["price_bucket"]),
        })
        if lineage_tables:
            log_iceberg_snapshots(lineage_tables)
            silver = lineage_tables.get(silver_interactions.TABLE_IDENTIFIER)
            if silver is not None:
                # ADR 0014: how much Silver this version saw. retrain_decision counts
                # "new rows" from here; refresh_baseline reads the snapshot id above.
                mlflow.log_param("silver_rows", table_row_count(silver))

        result = T.train(cfg, inter, items, seed, device=device, log=lambda m: print(json.dumps({"log": m})))
        samples = result["samples"]
        baseline_val_raw = popularity_metrics(samples["valid"].target, catalog.train_counts)
        baseline_test_raw = popularity_metrics(samples["test"].target, catalog.train_counts)
        baseline = {
            **_prefixed("baseline_pop_val", baseline_val_raw),
            **_prefixed("baseline_pop_test", baseline_test_raw),
        }
        # Per-epoch curves (step = epoch). The popularity baseline does not
        # train, so it is logged as a flat line at every epoch: same x-axis as
        # AGF, which lets MLflow overlay them in one chart.
        for row in result["history"]:
            step = row["epoch"]
            mlflow.log_metrics({
                "train_loss": row["loss"],
                "val_ndcg_at_10_epoch": row["valid_ndcg@10"],
                "val_recall_at_10_epoch": row["valid_recall@10"],
                "baseline_pop_val_ndcg_at_10_epoch": baseline_val_raw["ndcg@10"],
                "baseline_pop_val_recall_at_10_epoch": baseline_val_raw["recall@10"],
            }, step=step)

        # Quality numbers only. Run facts (sizes, epochs, time) are params:
        # a single value is not a metric and only produced one-bar charts.
        metrics = {
            **_prefixed("val", result["valid"]),
            **_prefixed("test", result["test"]),
            **baseline,
        }
        run_facts = {
            "best_epoch": result["best_epoch"],
            "epochs_run": result["epochs_run"],
            "train_seconds": round(result["train_s"], 2),
            "train_samples": len(samples["train"]),
            "val_samples": len(samples["valid"]),
        }
        if champion is not None:
            champion_metrics, champion_params = evaluate_champion(champion, samples, catalog.tokens)
            metrics.update(champion_metrics)
            run_facts.update(champion_params)

        # Per-sample validation NDCG, for the paired comparison between candidates.
        per_sample = ndcg_per_sample(result["model"], samples["valid"], 10, cfg.eval_batch_size, device)
        if len(per_sample) and abs(float(per_sample.mean()) - result["valid"]["ndcg@10"]) > 1e-6:
            raise RuntimeError("per-sample NDCG disagrees with trainer.evaluate")

        with tempfile.TemporaryDirectory(prefix="agf-export-") as tmp:
            exported = T.export(result, cfg, items, Path(tmp), seed)
            if exported["item_table_max_abs_diff"] >= MAX_TABLE_DIFF:
                raise RuntimeError(
                    f"item_table.npy disagrees with direct scores: {exported['item_table_max_abs_diff']}")
            mlflow.log_artifacts(tmp, artifact_path="agf")
        metrics["item_table_max_abs_diff"] = float(exported["item_table_max_abs_diff"])
        run_facts["n_params"] = int(exported["n_params"])
        mlflow.log_params(run_facts)
        mlflow.log_metrics(metrics)

        for figure, name in (
            (training_curves_figure(result["history"], result["best_epoch"], baseline_val_raw),
             "figures/training_curves.png"),
            (baseline_comparison_figure(result["valid"], result["test"], baseline_val_raw, baseline_test_raw),
             "figures/agf_vs_popularity.png"),
        ):
            mlflow.log_figure(figure, name)
            plt.close(figure)
        mlflow.log_dict(catalog.vocab_index, "item_vocab.json")

        model = result["model"].cpu().eval()
        model_info = mlflow.pytorch.log_model(model, name="model")
        out = {"run_id": run.info.run_id, **metrics, **run_facts}
        if champion is not None:
            out["champion_version"] = champion.version
        return TrainedCandidate(candidate_id, out, per_sample, int(exported["n_params"]), model_info.model_uri)


def _record_selection(client, trained: list[TrainedCandidate], selection) -> dict:
    """Write the outcome to MLflow: every run is tagged selected or not, the winner's run
    carries `selected_from`, the rule and every candidate's validation score."""
    by_id = {t.id: t for t in trained}
    for candidate in trained:
        selected = str(candidate.id == selection.winner).lower()
        client.set_tag(candidate.out["run_id"], "candidate_selected", selected)
    run_id = by_id[selection.winner].out["run_id"]
    client.log_param(run_id, "selected_from", ",".join(t.id for t in trained))
    client.log_param(run_id, "selected_candidate", selection.winner)
    client.log_param(run_id, "selection_rule", selection.rule)
    client.log_param(run_id, "selection_default", selection.default)
    for candidate in trained:
        value = float(candidate.val_ndcg_per_sample.mean())
        client.log_metric(run_id, f"candidate_{candidate.id}_val_ndcg_at_10", value)
        margin = selection.margins.get(candidate.id)
        if margin and margin["z"] is not None:
            client.log_metric(run_id, f"candidate_{candidate.id}_margin_se", margin["z"])
    own = selection.margins.get(selection.winner) or {}
    client.log_metric(run_id, "selection_margin_se", own.get("z") or 0.0)
    return {
        "winner": selection.winner,
        "default": selection.default,
        "rule": selection.rule,
        "margins": selection.margins,
        "candidates": {
            t.id: {"run_id": t.out["run_id"], "val_ndcg_at_10": float(t.val_ndcg_per_sample.mean()),
                   "test_ndcg_at_10": t.out["test_ndcg_at_10"], "n_params": t.n_params}
            for t in trained
        },
    }


def run_training(
    item_rows: list[dict],
    user_rows: list[dict],
    cfg: T.MethodConfig,
    *,
    seed: int = 2023,
    register: bool = True,
    device: str = "cpu",
    text: np.ndarray | None = None,
    lineage_tables: dict[str, Table] | None = None,
    champion: Champion | None = None,
    candidates: list[str] | None = None,
) -> dict:
    """Core routine, no Iceberg/MLflow-URI setup of its own (same split as
    `train_sequence.run_training`) so `tests/model/test_train_agf_smoke.py`
    can drive it with fixtures under a `file://` tracking URI.

    `champion` (the current production model, ADR 0013) is scored on the very
    samples each run validates/tests on; the numbers go into the run so
    `promote.py` can compare on one split.

    `candidates` (ADR 0015, default `["gru4rec"]` = the base config as before): ids
    from `candidates.CANDIDATE_OVERRIDES`. Each is trained on the same split in its
    own MLflow run; `candidates.select_winner` picks one on validation, and only that
    one is registered. With a single candidate nothing about the run changes."""
    ids = list(candidates) if candidates else parse_candidates(None)
    unknown = [i for i in ids if i not in CANDIDATE_OVERRIDES]
    if unknown:
        raise ValueError(f"unknown candidate(s) {unknown}")

    items_list, meta = catalog_meta(item_rows)
    inter = interactions_from_sequences(user_rows)
    catalog = build_catalog_items(inter, meta, items_list, cfg.use_text, text=text)
    items = to_item_inputs(catalog)

    mlflow.set_experiment(EXPERIMENT_NAME)
    group_id = uuid.uuid4().hex[:12]
    trained: list[TrainedCandidate] = []
    for candidate_id in ids:
        trained.append(_train_candidate(
            candidate_id, replace(cfg, **CANDIDATE_OVERRIDES[candidate_id]),
            inter=inter, catalog=catalog, items=items,
            n_items=len(items_list), n_user_rows=len(user_rows), seed=seed, device=device,
            lineage_tables=lineage_tables, champion=champion, group_id=group_id,
            run_name=candidate_id if len(ids) > 1 else None,
        ))

    selection_info = None
    if len(trained) == 1:
        winner = trained[0]
    else:
        selection = select_winner([
            Scored(t.id, float(t.val_ndcg_per_sample.mean()), t.val_ndcg_per_sample, t.n_params) for t in trained
        ])
        selection_info = _record_selection(mlflow.MlflowClient(), trained, selection)
        winner = next(t for t in trained if t.id == selection.winner)

    out = dict(winner.out)
    if selection_info is not None:
        out["selected_candidate"] = winner.id
        out["selection"] = selection_info
    if register:
        registered = mlflow.register_model(winner.model_uri, REGISTERED_MODEL_NAME)
        out["registered_model"] = REGISTERED_MODEL_NAME
        out["registered_version"] = registered.version
    return out


def load_gold_rows() -> tuple[list[dict], list[dict], dict[str, Table]]:
    catalog = get_catalog()
    item_table = catalog.load_table(gold_item_features.TABLE_IDENTIFIER)
    user_table = catalog.load_table(gold_user_features.TABLE_IDENTIFIER)
    item_rows = item_table.scan(selected_fields=ITEM_COLUMNS).to_arrow().to_pylist()
    user_rows = user_table.scan(selected_fields=USER_COLUMNS).to_arrow().to_pylist()
    lineage = {gold_item_features.TABLE_IDENTIFIER: item_table,
               gold_user_features.TABLE_IDENTIFIER: user_table,
               # Not read for training (Gold already derives from it): lineage only (ADR 0014).
               silver_interactions.TABLE_IDENTIFIER: catalog.load_table(silver_interactions.TABLE_IDENTIFIER)}
    return item_rows, user_rows, lineage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train AGF (ADR 0008; several candidates, ADR 0015) and register the best one in MLflow.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--seed", type=int, default=2023)
    parser.add_argument("--no-text", action="store_true", help="skip the MiniLM text branch (offline)")
    parser.add_argument("--epochs", type=int, default=None, help="override max epochs (quick runs)")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--candidates", default=os.getenv("TRAIN_CANDIDATES"),
                        help="comma-separated ids from jobs/training/candidates.py (default gru4rec; env "
                             "TRAIN_CANDIDATES): trains each, registers only the selected one")
    parser.add_argument("--no-champion", action="store_true",
                        help="do not score the production model on this run's split (promote.py then "
                             "falls back to the legacy comparison)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = T.MethodConfig.from_yaml(args.config)
    if args.no_text:
        cfg = replace(cfg, use_text=False)
    if args.epochs is not None:
        cfg = replace(cfg, epochs=args.epochs)
    candidates = parse_candidates(args.candidates)

    mlflow.set_tracking_uri(require_env("MLFLOW_TRACKING_URI"))
    item_rows, user_rows, lineage = load_gold_rows()
    # A production model that cannot be loaded must stop the run: training on
    # would make promote.py fall back to the cross-split comparison silently.
    champion = None if args.no_champion else load_production_champion(REGISTERED_MODEL_NAME)
    result = run_training(item_rows, user_rows, cfg, seed=args.seed, device=args.device,
                          lineage_tables=lineage, champion=champion, candidates=candidates)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
