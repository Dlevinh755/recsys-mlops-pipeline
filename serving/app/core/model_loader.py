"""Poll the MLflow registry for the `production` alias and keep the current
ranking model cached in memory — closes the train -> promote -> serve loop
described in `bao-cao-ky-thuat.md` mục 7.3: promoting a new model version
must not require restarting `serving`.

Model since ADR 0008: **AGF** (`reco_mlops_libs.ranking.agf`; GRU4Rec backbone by
default, SASRec backbone or other attribute fusion when `train_agf` selects another
candidate, ADR 0015 — scoring below only calls `net.seq_output`, so it is the same),
registry `REGISTERED_MODEL_NAME`. Scoring uses the item table
`H = [h_1..h_N]` that the training job precomputed and logged with the same
run (`agf/item_table.npy`) — `h_i` depends only on the item, so the online
cost is the sequence backbone plus one dot product per candidate, and serving
never needs item attributes or the text encoder:

    s_u = backbone(H[seq])       # user state from the ≤ max_len latest items
    score(u, i) = s_u · H[i]

Items that were in the catalog at training time but had no training
interaction (cold items) still have a row in `H` built from their attributes
(`e^id = 0`, `π = 0`), so they are scored instead of falling back. Items
added to the catalog *after* training are not in `item_vocab.json` and get
the neutral 0.0 score, as before (the next retrain picks them up).

`ServingRanker` implements `RankingModel.predict()` (CLAUDE.md #7) and
sources the input sequence from `sequence_source.py` (Feast + Redis,
Hướng B) instead of scanning Iceberg — fine for a training-time self-check,
not for a low-latency request path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import mlflow.pytorch
import numpy as np
import torch

from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version
from reco_mlops_libs.ranking.base import REGISTERED_MODEL_NAME, RankingModel
from serving.app.core.sequence_source import fetch_historical_sequence, fetch_recent_items, merge_sequence, to_indices

ITEM_TABLE_ARTIFACT = "agf/item_table.npy"
VOCAB_ARTIFACT = "item_vocab.json"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedModel:
    net: torch.nn.Module  # reco_mlops_libs.ranking.agf.agf_core.AGFSequentialModel
    vocab: dict[str, int]
    item_table: torch.Tensor  # H, shape (len(vocab) + 1, d); row 0 = [PAD] = 0
    version: str


def score_candidates(
    net: torch.nn.Module, item_table: torch.Tensor, history: list[int], candidates: list[int | None]
) -> list[float]:
    """Scores for `candidates` (vocab indices, `None` = unknown → 0.0) given
    the user's `history` (vocab indices, oldest first, non-empty). Only the
    latest `net.max_len` items are fed to the backbone, as in training."""
    history = history[-int(getattr(net, "max_len", len(history))):]
    with torch.no_grad():
        seq = torch.tensor([history], dtype=torch.long)
        lengths = torch.tensor([len(history)], dtype=torch.long)
        state = net.seq_output(seq, lengths, item_table)[0]  # (d,)
        known = [index for index in candidates if index is not None]
        known_scores = (item_table[known] @ state).tolist() if known else []
    it = iter(known_scores)
    return [next(it) if index is not None else 0.0 for index in candidates]


class ServingRanker(RankingModel):
    def __init__(self, loaded: LoadedModel, redis_client, feature_store) -> None:
        self._loaded = loaded
        self._redis_client = redis_client
        self._feature_store = feature_store

    def predict(self, user_id: str, candidate_items: list[str]) -> list[float]:
        historical = fetch_historical_sequence(self._feature_store, user_id)
        recent = fetch_recent_items(self._redis_client, user_id)
        sequence = merge_sequence(historical, recent)
        indices = to_indices(sequence, self._loaded.vocab)
        if not indices:
            # No usable history at all (true cold-start user) — the caller
            # (homepage.py) is responsible for falling back to popularity
            # instead of trusting this neutral score.
            return [0.0] * len(candidate_items)
        vocab = self._loaded.vocab
        return score_candidates(
            self._loaded.net, self._loaded.item_table, indices, [vocab.get(item) for item in candidate_items]
        )


def load_item_table(path: str, vocab: dict[str, int]) -> torch.Tensor:
    table = torch.from_numpy(np.load(path)).float()
    if table.shape[0] != len(vocab) + 1:
        raise ValueError(f"item_table has {table.shape[0]} rows, expected {len(vocab) + 1} (vocab + [PAD])")
    return table


class ModelLoader:
    def __init__(self, poll_interval_seconds: int) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self._current: LoadedModel | None = None
        self._task: asyncio.Task | None = None

    def get_current(self) -> LoadedModel | None:
        return self._current

    def get_ranker(self, redis_client, feature_store) -> RankingModel | None:
        if self._current is None:
            return None
        return ServingRanker(self._current, redis_client, feature_store)

    def _load_once(self) -> None:
        client = get_client()
        version = get_production_version(REGISTERED_MODEL_NAME, client=client)
        if version is None:
            logger.info("No production version for %s yet", REGISTERED_MODEL_NAME)
            return
        if self._current is not None and self._current.version == version.version:
            return  # unchanged since last poll, nothing to reload
        net = mlflow.pytorch.load_model(f"models:/{REGISTERED_MODEL_NAME}/{version.version}")
        net.eval()
        vocab_path = client.download_artifacts(version.run_id, VOCAB_ARTIFACT)
        with open(vocab_path, encoding="utf-8") as file:
            vocab: dict[str, int] = json.load(file)
        item_table = load_item_table(client.download_artifacts(version.run_id, ITEM_TABLE_ARTIFACT), vocab)
        self._current = LoadedModel(net=net, vocab=vocab, item_table=item_table, version=version.version)
        logger.info("Loaded %s version %s", REGISTERED_MODEL_NAME, version.version)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                await asyncio.to_thread(self._load_once)
            except Exception:  # noqa: BLE001 - polling must never crash the loop
                logger.exception("Model poll failed; keeping previous version (if any)")

    def start(self) -> None:
        try:
            self._load_once()  # best-effort so early requests can use it right away
        except Exception:  # noqa: BLE001 - startup must not crash if MLflow is briefly unreachable
            logger.exception("Initial model load failed; will retry on next poll")
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
