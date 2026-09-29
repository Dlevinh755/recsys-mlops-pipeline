"""GRU4Rec network architecture — shared between `jobs/training/` (trains
it) and `serving/` (loads a trained instance for inference), which is why
this class lives in `libs/` rather than in `jobs/training/train_sequence.py`
where it was originally defined (Phase 4).

MLflow saves a PyTorch model via `mlflow.pytorch.log_model()`, which pickles
the module — unpickling later requires importing the *exact same class at
the exact same module path* in whatever process calls
`mlflow.pytorch.load_model()`. `serving` is a separate Docker image that
must not install `jobs/training/` (CLAUDE.md principle 2 — one image per
job group, no cross-image dependency), so the class the model was pickled
against has to live somewhere both images install: `libs/`.

`torch` is deliberately **not** a dependency of the `libs` package itself
(see `libs/pyproject.toml`) — only `jobs/training/` and `serving/` import
this module, and both already declare `torch` in their own
`requirements/*.txt`. Any other consumer of `reco_mlops_libs` (extract,
transform, materialize) never imports `reco_mlops_libs.ranking.gru4rec`, so
it is never forced to install torch.
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence

# Index 0 is reserved for padding — real vocabulary items start at 1 (see
# `jobs/training/build_sequence_dataset.py::build_item_vocab`).
PAD_INDEX = 0


class GRU4RecNet(nn.Module):
    """Embedding -> GRU (1 layer) -> Linear, per
    `docs/bao-cao-ky-thuat.md` mục 7.6. The Linear layer projects the GRU's
    final hidden state into the *embedding* space rather than into a full
    `vocab_size`-wide logit space — scoring is a dot product against
    (a subset of) the same embedding table (weight tying), which is what
    makes sampled softmax cheap during training (see
    `jobs/training/train_sequence.py`) and full-catalog scoring
    (`score_all`) cheap enough to run per-request in `serving`.
    """

    def __init__(self, vocab_size: int, embedding_dim: int, hidden_size: int) -> None:
        super().__init__()
        self.item_embedding = nn.Embedding(vocab_size, embedding_dim, padding_idx=PAD_INDEX)
        self.gru = nn.GRU(embedding_dim, hidden_size, num_layers=1, batch_first=True)
        self.output_proj = nn.Linear(hidden_size, embedding_dim)

    def encode(self, inputs: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        """Project each padded input sequence to a single embedding-space
        vector, taken from the GRU's hidden state at each sequence's *own*
        last valid position (via `pack_padded_sequence`, not the padded
        tail)."""
        embedded = self.item_embedding(inputs)
        packed = pack_padded_sequence(
            embedded, lengths.cpu(), batch_first=True, enforce_sorted=False
        )
        _, hidden = self.gru(packed)
        return self.output_proj(hidden[-1])

    def score_all(self, inputs: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        """Score every item in the vocabulary — full softmax over all
        classes. Used by `jobs/training/evaluate.py` and, at this data
        scale (~1.5k items), cheaply reusable per-request in `serving`."""
        projected = self.encode(inputs, lengths)
        return projected @ self.item_embedding.weight.T
