"""Phase 5 — item-item similarity from the production GRU4Rec's own item
embeddings (cosine similarity), materialized to Redis for low-latency
lookup by `serving/app/core/recency_boost.py` and `GET /recommend/similar`.

Runs inside the existing `training-job` image (already has `torch` to read
embeddings and `mlflow` to load the model) — combines what the original
plan split into two files (`jobs/candidates/similar_items.py` +
`jobs/materialize/export_similar_items_to_redis.py`) into one, avoiding a
new Docker image for one small script. See
`docs/modules/phase-5-serving.md` for the scope-narrowing rationale.

The vocab used here is loaded from the **same MLflow run** that produced
the production model (`item_vocab.json` artifact), not recomputed from the
current catalog — recomputing independently could assign different indices
if the catalog changed since training, silently misaligning with the
embedding table's actual rows.
"""

from __future__ import annotations

import argparse
import json

import mlflow.pytorch
import redis
import torch
import torch.nn.functional as functional

from jobs.training.train_sequence import REGISTERED_MODEL_NAME
from reco_mlops_libs.common.env import get_env, require_env
from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version
from reco_mlops_libs.ranking.gru4rec import GRU4RecNet

REDIS_KEY_PREFIX = "similar_items"


def load_production_model_and_vocab() -> tuple[GRU4RecNet, dict[str, int]]:
    client = get_client()
    version = get_production_version(REGISTERED_MODEL_NAME, client=client)
    if version is None:
        raise RuntimeError(
            f"No production version for {REGISTERED_MODEL_NAME!r} — train + promote first."
        )
    net = mlflow.pytorch.load_model(f"models:/{REGISTERED_MODEL_NAME}/{version.version}")
    vocab_path = client.download_artifacts(version.run_id, "item_vocab.json")
    with open(vocab_path, encoding="utf-8") as file:
        vocab: dict[str, int] = json.load(file)
    return net, vocab


def compute_top_k_similar(
    net: GRU4RecNet, vocab: dict[str, int], top_k: int
) -> dict[str, list[str]]:
    """Cosine similarity over every pair of items in `vocab` (cheap at this
    catalog size — no approximate nearest-neighbor index needed)."""
    index_to_product = {index: product_id for product_id, index in vocab.items()}
    indices = sorted(index_to_product)  # excludes PAD_INDEX (0) implicitly

    embeddings = net.item_embedding.weight.detach()[indices]
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
        description="Materialize item-item similarity (from GRU4Rec embeddings) into Redis."
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--ttl-days", type=int, default=7)
    args = parser.parse_args()

    net, vocab = load_production_model_and_vocab()
    net.eval()
    with torch.no_grad():
        similar = compute_top_k_similar(net, vocab, args.top_k)

    redis_client = redis.Redis(
        host=require_env("REDIS_HOST"), port=int(get_env("REDIS_PORT", "6379"))
    )
    written = push_to_redis(similar, redis_client, args.ttl_days * 86400)

    print(json.dumps({"items_processed": len(similar), "redis_keys_written": written}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
