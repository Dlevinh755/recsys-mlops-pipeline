"""Phase 10 (ADR 0008) — turn Gold rows into the inputs of
`reco_mlops_libs.ranking.agf` (AGF-GRU4Rec). Pure pandas/numpy — no torch,
mlflow or Iceberg import — so the mapping is unit-testable anywhere
(`tests/unit/training/test_agf_inputs.py`).

Mapping from the pipeline's columns to the experiment's attribute rules
(`reco_mlops_libs/ranking/agf/features.py`, unchanged):

| AGF field (`features.py`) | Pipeline source (`gold.item_features`) |
|---|---|
| `categories` (list, ≤ 3 levels) | `category_path` split on `" > "`; empty → `category` |
| `main_category` (fallback)      | `category` |
| `store`                         | `brand` |
| `price`                         | `price` |
| `title` (text)                  | `title` |

Interactions come from `gold.user_features` (the same ≤ 10-item sequences
serving reads from Feast), exploded back to `(user_id, item_id, timestamp)`
rows. The item catalog is the **full** `gold.item_features` table — items
that never appear in a training prefix still get a row in `H`, scored from
their attributes (`e^id = 0`, `π = 0`), which is the point of AGF for the
cold-item share in `amazone_data/DATA.md` 4.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from reco_mlops_libs.ranking.agf import features as F

CATEGORY_PATH_SEPARATOR = " > "  # must match scripts/seed_source_db.py
PAD = "[PAD]"


def split_category_path(path: object) -> list[str] | None:
    if path is None or (isinstance(path, float) and np.isnan(path)):
        return None
    levels = [level.strip() for level in str(path).split(CATEGORY_PATH_SEPARATOR)]
    levels = [level for level in levels if level]
    return levels or None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # Decimal from Iceberg DecimalType(12, 2)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def catalog_meta(item_rows: list[dict]) -> tuple[list[str], pd.DataFrame]:
    """`gold.item_features` rows → (item ids sorted, metadata frame in the
    column shape `features.py` expects)."""
    rows = sorted(item_rows, key=lambda row: row["product_id"])
    items = [row["product_id"] for row in rows]
    if len(set(items)) != len(items):
        raise ValueError("gold.item_features has duplicate product_id")
    meta = pd.DataFrame({
        "item_id": items,
        "title": [row.get("title") for row in rows],
        "main_category": [row.get("category") for row in rows],
        "categories": [split_category_path(row.get("category_path")) for row in rows],
        "store": [row.get("brand") for row in rows],
        "price": [_to_float(row.get("price")) for row in rows],
    })
    return items, meta


def sort_interactions(inter: pd.DataFrame) -> pd.DataFrame:
    """Same ordering as `trainer.sort_interactions`: (user, timestamp, row)."""
    inter = inter.reset_index(drop=True)
    inter = inter.assign(_row=np.arange(len(inter)))
    return (inter.sort_values(["user_id", "timestamp", "_row"], kind="stable")
            .drop(columns="_row").reset_index(drop=True))


def interactions_from_sequences(user_rows: list[dict]) -> pd.DataFrame:
    """`gold.user_features` rows → `(user_id, item_id, timestamp)` rows.

    `timestamp` is the event time (seconds) when `event_time_sequence` is
    present, otherwise the position in the sequence (fixtures)."""
    users, items, stamps = [], [], []
    for row in user_rows:
        sequence = list(row["item_sequence"])
        times = row.get("event_time_sequence")
        times = list(times) if times is not None else None
        for position, item in enumerate(sequence):
            users.append(row["user_id"])
            items.append(item)
            if times is not None:
                t = times[position]
                stamps.append(pd.Timestamp(t).timestamp())
            else:
                stamps.append(float(position))
    inter = pd.DataFrame({"user_id": users, "item_id": items, "timestamp": stamps})
    return sort_interactions(inter)


@dataclass
class CatalogItems:
    """Field-for-field the same as `trainer.ItemInputs` (index 0 = `[PAD]`)."""

    tokens: list[str]
    categories: np.ndarray
    store: np.ndarray
    price: np.ndarray
    text: np.ndarray | None
    train_counts: np.ndarray
    vocab: dict
    price_edges: list[float]

    @property
    def vocab_index(self) -> dict[str, int]:
        """`product_id -> index` (1-based), the `item_vocab.json` format the
        rest of the pipeline (serving, monitoring) already reads."""
        return {token: index for index, token in enumerate(self.tokens) if index > 0}


def build_catalog_items(
    inter: pd.DataFrame,
    meta: pd.DataFrame,
    items: list[str],
    use_text: bool,
    text: np.ndarray | None = None,
    encode_texts: Callable[[list[str]], np.ndarray] | None = None,
) -> CatalogItems:
    """Like `trainer.prepare_items`, but over the full catalog `items`
    (not only items that appear in `inter`). Vocab, `[UNK]` thresholds and
    price-bucket edges are fit again on every call (every training run)."""
    unknown = set(inter["item_id"]) - set(items)
    if unknown:
        raise KeyError(f"{len(unknown)} interacted items are missing from gold.item_features")
    feats, edges = F.build_item_features(meta, items)
    vocab = F.fit_vocab(feats)
    enc = F.encode_features(feats, vocab)
    if use_text and text is None:
        texts = F.build_texts(meta, items)
        text = (encode_texts or F.encode_texts)(texts)
    if use_text and len(text) != len(items):
        raise ValueError("text rows must match catalog items")

    def pad(array: np.ndarray) -> np.ndarray:
        return np.concatenate([np.zeros((1,) + array.shape[1:], dtype=array.dtype), array])

    counts = F.train_counts(sort_interactions(inter), items)
    return CatalogItems(
        tokens=[PAD, *items],
        categories=pad(enc["categories"]),
        store=pad(enc["store"]),
        price=pad(enc["price"]),
        text=pad(np.asarray(text, dtype=np.float32)) if use_text else None,
        train_counts=pad(counts),
        vocab=vocab,
        price_edges=edges,
    )
