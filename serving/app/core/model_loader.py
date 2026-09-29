"""Poll the MLflow registry for the `production` alias and keep the current
GRU4Rec model cached in memory — closes the train -> promote -> serve loop
described in `bao-cao-ky-thuat.md` mục 7.3: promoting a new model version
must not require restarting `serving`.

`ServingRanker` implements `RankingModel.predict()` (the same interface
`jobs/training/train_sequence.py::GRU4RecRanker` implements for its own
self-check) but sources the input sequence from `sequence_source.py`
(Feast + Redis, Hướng B) instead of scanning Iceberg directly — Iceberg
scans are fine for a one-off training-time self-check, not for a
low-latency request path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import mlflow.pytorch
import torch

from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version
from reco_mlops_libs.ranking.base import RankingModel
from reco_mlops_libs.ranking.gru4rec import GRU4RecNet
from serving.app.core.sequence_source import fetch_historical_sequence, fetch_recent_items, merge_sequence, to_indices

REGISTERED_MODEL_NAME = "reco-mlops-gru4rec"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedModel:
    net: GRU4RecNet
    vocab: dict[str, int]
    version: str


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

        net = self._loaded.net
        net.eval()
        with torch.no_grad():
            input_tensor = torch.tensor([indices], dtype=torch.long)
            lengths = torch.tensor([len(indices)], dtype=torch.long)
            projected = net.encode(input_tensor, lengths)  # (1, D)
            scores: list[float] = []
            for item in candidate_items:
                index = self._loaded.vocab.get(item)
                if index is None:
                    scores.append(0.0)
                    continue
                embedding = net.item_embedding(torch.tensor([index]))
                scores.append((projected @ embedding.T).item())
        return scores


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
        vocab_path = client.download_artifacts(version.run_id, "item_vocab.json")
        with open(vocab_path, encoding="utf-8") as file:
            vocab: dict[str, int] = json.load(file)
        self._current = LoadedModel(net=net, vocab=vocab, version=version.version)
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
