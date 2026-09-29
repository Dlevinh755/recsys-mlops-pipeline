"""Regenerates `item_catalog.json`/`user_sequences.json` — fixed seed (42),
60 items / 120 users / ~750 interactions, same shape as
`gold.item_features`/`gold.user_features` (`product_id` list;
`user_id`/`item_sequence`/`sequence_length` rows) but small enough to train
1 epoch in well under a second on CPU. Not run automatically — these files
are committed fixtures; re-run only if the fixture needs to change size/shape.

    python tests/model/sample_data/generate.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

random.seed(42)
N_ITEMS = 60
N_USERS = 120
MAX_LEN = 10
OUT_DIR = Path(__file__).resolve().parent


def main() -> None:
    items = [f"item_{i:03d}" for i in range(N_ITEMS)]
    sequences = []
    for u in range(N_USERS):
        length = random.randint(2, MAX_LEN)
        seq = [random.choice(items) for _ in range(length)]
        sequences.append({"user_id": f"user_{u:03d}", "item_sequence": seq, "sequence_length": length})

    (OUT_DIR / "item_catalog.json").write_text(json.dumps(items, indent=2))
    (OUT_DIR / "user_sequences.json").write_text(json.dumps(sequences, indent=2))
    print(f"items={len(items)} users={len(sequences)} interactions={sum(s['sequence_length'] for s in sequences)}")


if __name__ == "__main__":
    main()
