"""Item-item similarity (cosine) from the production model's item
representations, materialized to Redis for low-latency lookup by
`serving/app/core/recency_boost.py` and `GET /recommend/similar`.

Since ADR 0008 the representation is AGF's precomputed item table
`H = [h_1..h_N]` (`agf/item_table.npy`, logged by `jobs/training/train_agf.py`
in the same run as the model), not a raw ID embedding table — `h_i` mixes
the ID embedding with the item's attributes, so items with no training
interaction (cold items, `e^id = 0`) still get a meaningful neighbor list
from their category/brand/price/title instead of noise.

Runs inside the existing `training-job` image (already has `torch` and
`mlflow`) — combines what the original plan split into two files
(`jobs/candidates/similar_items.py` +
`jobs/materialize/export_similar_items_to_redis.py`) into one, avoiding a
new Docker image for one small script. See
`docs/modules/phase-5-serving.md` for the scope-narrowing rationale.

The vocab and item table are loaded from the **same MLflow run** that
produced the production model, not recomputed from the current catalog —
recomputing independently could assign different indices if the catalog
changed since training, silently misaligning rows.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import redis
import torch
import torch.nn.functional as functional

from reco_mlops_libs.common.env import get_env, require_env
from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version
from reco_mlops_libs.ranking.base import REGISTERED_MODEL_NAME

ITEM_TABLE_ARTIFACT = "agf/item_table.npy"
REDIS_KEY_PREFIX = "similar_items"


def load_production_item_table_and_vocab() -> tuple[torch.Tensor, dict[str, int]]:
    client = get_client()
    version = get_production_version(REGISTERED_MODEL_NAME, client=client)
    if version is None:
        raise RuntimeError(
            f"No production version for {REGISTERED_MODEL_NAME!r} — train + promote first."
        )
    vocab_path = client.download_artifacts(version.run_id, "item_vocab.json")
    with open(vocab_path, encoding="utf-8") as file:
        vocab: dict[str, int] = json.load(file)
    item_table = torch.from_numpy(np.load(client.download_artifacts(version.run_id, ITEM_TABLE_ARTIFACT))).float()
    return item_table, vocab


def compute_top_k_similar(
    item_table: torch.Tensor, vocab: dict[str, int], top_k: int
) -> dict[str, list[str]]:
    """Cosine similarity over every pair of items in `vocab` (cheap at this
    catalog size — no approximate nearest-neighbor index needed)."""
    index_to_product = {index: product_id for product_id, index in vocab.items()}
    indices = sorted(index_to_product)  # excludes PAD_INDEX (0) implicitly

    embeddings = item_table[indices]
    normalized = functional.normalize(embeddings, dim=1)
    similarity = normalized @ normalized.T  # (N, N)
    similarity.fill_diagonal_(-float("inf"))  # never recommend an item as similar to itself

    k = min(top_k, len(indices) - 1)
    top_indices = torch.topk(similarity, k, dim=1).indices  # (N, k)

    return {
        index_to_product[indices[row]]: [index_to_product[indices[col]] for col in top_indices[row].tolist()]
        for row in range(len(indices))
    }


def push_to_redis(
    similar: dict[str, list[str]], client: redis.Redis, ttl_seconds: int
) -> int:
    pipeline = client.pipeline()
    for product_id, similar_ids in similar.items():
        pipeline.set(f"{REDIS_KEY_PREFIX}:{product_id}", json.dumps(similar_ids), ex=ttl_seconds)
    pipeline.execute()
    return len(similar)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize item-item similarity (from the AGF item table H) into Redis."
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--ttl-days", type=int, default=7)
    args = parser.parse_args()

    item_table, vocab = load_production_item_table_and_vocab()
    with torch.no_grad():
        similar = compute_top_k_similar(item_table, vocab, args.top_k)

    redis_client = redis.Redis(
        host=require_env("REDIS_HOST"), port=int(get_env("REDIS_PORT", "6379"))
    )
    written = push_to_redis(similar, redis_client, args.ttl_days * 86400)

    print(json.dumps({"items_processed": len(similar), "redis_keys_written": written}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
