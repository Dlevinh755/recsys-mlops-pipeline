"""Model-agnostic ranking interface — added in Phase 4 (Training & Model
Registry), see `docs/de-xuat-trien-khai.md` Phase 4 checklist and
`docs/bao-cao-ky-thuat.md` mục 7.5.

The serving layer (Phase 5, not built yet) must call models only through
`RankingModel.predict()` — never `model.forward(tensor)` or
`model.predict(dataframe)` directly. This is CLAUDE.md architecture
principle 7: swapping the ranking model (e.g. GRU4Rec today, something else
later) must not require touching serving code, only the model
implementation and which version the registry marks as `production`.

This module intentionally has zero dependency on torch/mlflow/pandas — a
concrete model's own input shape (DataFrame, tensor, raw sequence) is a
detail of its implementation, not part of this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class RankingModel(ABC):
    """Thin interface every ranking model implementation must satisfy."""

    @abstractmethod
    def predict(self, user_id: str, candidate_items: list[str]) -> list[float]:
        """Return one score per entry in `candidate_items`, same order.

        A higher score means a stronger recommendation. Implementations may
        return a neutral/fallback score (rather than raising) for a
        `user_id` or item unseen during training — the exact fallback
        policy belongs to the implementation, not this interface.
        """
        raise NotImplementedError
