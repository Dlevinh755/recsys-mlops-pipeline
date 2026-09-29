"""Phase 4 — turn `gold.user_features` into next-item-prediction examples
for `train_sequence.py` (GRU4Rec). See
`docs/decisions/0004-gru-sequence-model-thay-lightgbm.md` for why this reads
a per-user item sequence instead of a flat feature table.

Reads two Gold tables directly via `catalog.load_table()` (not
`get_or_create_table()` — both must already exist from Phase 2/3; a missing
table should fail loudly, not be silently created empty):

- `gold.item_features` — only used for its full `product_id` catalog, to
  build the item vocabulary (every product gets an embedding, not just ones
  that appear in some user's sequence — needed for Coverage and for
  scoring arbitrary candidate items later in Phase 5).
- `gold.user_features` — the actual `item_sequence` per user.

Splitting strategy — **leave-last-item-out per user**, the standard
evaluation protocol for sequential recommendation (equivalent to "split by
time" since it holds out each user's most recent interaction):

- `sequence_length < 2` → user contributes nothing (not enough items for a
  prefix -> target pair, nor for a validation example).
- Otherwise, the last item is the validation target (context = every item
  before it); every earlier position becomes one training example
  (context = items before it, target = that item). A user with exactly 2
  items therefore contributes a validation example but zero training
  examples — accepted at this data scale.

`build_dataset()` is a pure function (no I/O beyond the two tables already
loaded) so `train_sequence.py` can import and call it directly in the same
process — recomputing this split is cheap, so there is no intermediate
dataset artifact to persist (nothing here violates CLAUDE.md #6: there is no
state, just an in-memory function call).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pyiceberg.table import Table

from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_item_features, gold_user_features
from reco_mlops_libs.ranking.gru4rec import PAD_INDEX  # noqa: F401 (re-exported)


@dataclass(frozen=True)
class Example:
    input_indices: tuple[int, ...]
    target_index: int


@dataclass(frozen=True)
class SequenceDataset:
    vocab: dict[str, int]
    train_examples: tuple[Example, ...]
    val_examples: tuple[Example, ...]
    skipped_users: int


def build_item_vocab(item_features_table: Table) -> dict[str, int]:
    """`product_id -> index`, 1-based (0 is PAD), covering the full catalog
    from `gold.item_features` — not just items seen in some sequence."""
    arrow = item_features_table.scan(selected_fields=("product_id",)).to_arrow()
    product_ids = sorted(set(arrow.column("product_id").to_pylist()))
    return {product_id: index for index, product_id in enumerate(product_ids, start=1)}


def build_dataset(user_features_table: Table, vocab: dict[str, int]) -> SequenceDataset:
    arrow = user_features_table.scan(
        selected_fields=("user_id", "item_sequence", "sequence_length")
    ).to_arrow()
    rows = arrow.to_pylist()

    train_examples: list[Example] = []
    val_examples: list[Example] = []
    skipped_users = 0

    for row in rows:
        length = row["sequence_length"]
        if length < 2:
            skipped_users += 1
            continue
        # `item_sequence` references `product_id`s that exist in
        # `gold.item_features` by foreign key at the source-db level — a
        # KeyError here means real data drift between the two Gold tables,
        # which should fail loudly rather than be papered over.
        indices = [vocab[product_id] for product_id in row["item_sequence"]]

        val_examples.append(Example(tuple(indices[:-1]), indices[-1]))
        for t in range(1, length - 1):
            train_examples.append(Example(tuple(indices[:t]), indices[t]))

    return SequenceDataset(
        vocab=vocab,
        train_examples=tuple(train_examples),
        val_examples=tuple(val_examples),
        skipped_users=skipped_users,
    )


def load_dataset() -> SequenceDataset:
    """Convenience entry point: load both Gold tables and build the split.
    Used by `train_sequence.py` and `evaluate.py` so both rebuild the exact
    same split from the exact same source of truth."""
    catalog = get_catalog()
    item_features_table = catalog.load_table(gold_item_features.TABLE_IDENTIFIER)
    user_features_table = catalog.load_table(gold_user_features.TABLE_IDENTIFIER)
    vocab = build_item_vocab(item_features_table)
    return build_dataset(user_features_table, vocab)


def main() -> int:
    dataset = load_dataset()
    print(json.dumps({
        "vocab_size": len(dataset.vocab) + 1,  # +1 for PAD_INDEX
        "train_examples": len(dataset.train_examples),
        "val_examples": len(dataset.val_examples),
        "skipped_users": dataset.skipped_users,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
