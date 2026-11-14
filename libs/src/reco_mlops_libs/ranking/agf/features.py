# Nguồn: repo thực nghiệm `experiment/` — src/expkit/handoff/features.py, commit 58d50db (58d50db0f9d21dfc735f0337c3870d67ccff7aa4).
# Chép nguyên theo ADR 0008; chỉ đổi import cho khớp package `reco_mlops_libs`.
# Sửa logic ở repo thực nghiệm trước rồi chép lại, không sửa riêng ở đây.
"""Quy tắc tạo thuộc tính item cho AGF (bàn giao sang pipeline; thiết kế mục 3.3).

Chỉ dùng numpy/pandas (text: sentence-transformers, import muộn); KHÔNG import
RecBole hay phần còn lại của expkit, để chép nguyên file sang `libs/`.
Quy tắc trùng với expkit/data/prepare.py (S1); tests/test_handoff.py kiểm tra
hai bản cho cùng kết quả. Vocab và biên bucket giá FIT LẠI mỗi lần train.

    feats, edges = build_item_features(meta, items)      # bảng token theo thứ tự `items`
    vocab = fit_vocab(feats)                             # token → chỉ số, 0 = [PAD]
    arrays = encode_features(feats, vocab)               # categories [N, 3], store [N], price [N]
    text = encode_texts(build_texts(meta, items))        # [N, 384], chuẩn hoá L2
    counts = train_counts(interactions, items)           # chỉ đếm phần TRAIN (leave-one-out)

Không bao giờ đọc average_rating, rating_number, bought_together (rò rỉ).
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

MIN_TOKEN_COUNT = 5  # categories: token xuất hiện ở dưới 5 item → [UNK]
MIN_STORE_ITEMS = 5  # store có dưới 5 item → [UNK]
N_PRICE_BUCKETS = 10
MAX_CATEGORY_LEVELS = 3
UNK = "[UNK]"
MISSING = "[MISSING]"
PAD = "[PAD]"
META_FIELDS = ("parent_asin", "title", "main_category", "categories", "store", "price")
FORBIDDEN_FIELDS = ("average_rating", "rating_number", "bought_together")
TEXT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
ATTRIBUTES = ("categories", "store", "price_bucket")


def _clean(value) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    s = " ".join(str(value).split())
    return s or None


def raw_categories(categories, main_category) -> list[str]:
    """Tối đa 3 cấp đầu của categories; rỗng thì dùng main_category."""
    cats = [c for c in (_clean(x) for x in (categories or [])) if c][:MAX_CATEGORY_LEVELS]
    if not cats and _clean(main_category):
        cats = [_clean(main_category)]
    return cats


def parse_price(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        p = float(value)
    else:
        try:
            p = float(str(value).replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
    return p if np.isfinite(p) and p > 0 else None


def price_edges(prices: list[float]) -> list[float]:
    qs = np.linspace(0, 1, N_PRICE_BUCKETS + 1)[1:-1]
    return [float(x) for x in np.quantile(np.asarray(prices, dtype=float), qs)]


def price_bucket(p: float | None, edges: list[float]) -> str:
    if p is None:
        return MISSING
    return f"p{int(np.searchsorted(edges, p, side='right'))}"


def _meta_frame(meta: pd.DataFrame, items: list[str]) -> pd.DataFrame:
    """Metadata (cột parent_asin hoặc item_id) xếp theo `items`; bỏ cột cấm."""
    meta = meta.rename(columns={"parent_asin": "item_id"})
    meta = meta[[c for c in meta.columns if c not in FORBIDDEN_FIELDS]]
    return meta.drop_duplicates("item_id", keep="first").set_index("item_id").reindex(items)


def build_item_features(meta: pd.DataFrame, items: list[str]) -> tuple[pd.DataFrame, list[float]]:
    """Bảng (item_id, categories, store, price_bucket) theo thứ tự `items` và biên bucket giá."""
    meta = _meta_frame(meta, items)
    cats = [raw_categories(c if isinstance(c, list) else None, m)
            for c, m in zip(meta["categories"], meta["main_category"])]
    cat_tokens = [[c.replace(" ", "_") for c in cs] for cs in cats]
    token_freq = Counter(t for ts in cat_tokens for t in set(ts))
    categories = []
    for ts in cat_tokens:
        mapped = list(dict.fromkeys(t if token_freq[t] >= MIN_TOKEN_COUNT else UNK for t in ts))
        categories.append(" ".join(mapped) if mapped else MISSING)

    stores_raw = [_clean(s) for s in meta["store"]]
    store_freq = Counter(s for s in stores_raw if s)
    stores = [MISSING if s is None else (s if store_freq[s] >= MIN_STORE_ITEMS else UNK) for s in stores_raw]

    prices = [parse_price(p) for p in meta["price"]]
    edges = price_edges([p for p in prices if p is not None])
    buckets = [price_bucket(p, edges) for p in prices]
    feats = pd.DataFrame({"item_id": list(items), "categories": categories, "store": stores, "price_bucket": buckets})
    return feats, edges


def build_texts(meta: pd.DataFrame, items: list[str]) -> list[str]:
    """Text = title | categories thô (nối bằng ' > '); thiếu phần nào bỏ phần đó."""
    meta = _meta_frame(meta, items)
    out = []
    for title, c, m in zip(meta["title"], meta["categories"], meta["main_category"]):
        cats = " > ".join(raw_categories(c if isinstance(c, list) else None, m))
        parts = [p for p in (_clean(title), cats) if isinstance(p, str) and p]
        out.append(" | ".join(parts))
    return out


def encode_texts(texts: list[str], device: str | None = None, batch_size: int = 256) -> np.ndarray:
    from sentence_transformers import SentenceTransformer  # import muộn: nặng

    model = SentenceTransformer(TEXT_MODEL, device=device)
    emb = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, convert_to_numpy=True,
                       show_progress_bar=False)
    return emb.astype(np.float32)


# ---------------------------------------------------------------- vocab + mã hoá
def fit_vocab(feats: pd.DataFrame) -> dict[str, dict[str, int]]:
    """{thuộc tính: {token: chỉ số}}, 0 = [PAD]; token theo thứ tự xuất hiện."""
    vocab = {}
    for field in ATTRIBUTES:
        tokens = feats[field].str.split(" ").explode() if field == "categories" else feats[field]
        vocab[field] = {PAD: 0, **{t: i + 1 for i, t in enumerate(dict.fromkeys(tokens))}}
    return vocab


def encode_features(feats: pd.DataFrame, vocab: dict, max_categories: int = MAX_CATEGORY_LEVELS) -> dict:
    """Mảng chỉ số theo thứ tự hàng của `feats`; token lạ (item mới) → [UNK] nếu có, ngược lại 0."""
    def idx(field, tok):
        v = vocab[field]
        return v.get(tok, v.get(UNK, 0))

    cats = np.zeros((len(feats), max_categories), dtype=np.int64)
    for r, value in enumerate(feats["categories"]):
        toks = value.split(" ")[:max_categories]
        cats[r, :len(toks)] = [idx("categories", t) for t in toks]
    return {"categories": cats,
            "store": np.array([idx("store", t) for t in feats["store"]], dtype=np.int64),
            "price": np.array([idx("price_bucket", t) for t in feats["price_bucket"]], dtype=np.int64)}


# ---------------------------------------------------------------- độ phổ biến
def train_counts(interactions: pd.DataFrame, items: list[str]) -> np.ndarray:
    """Số lần mỗi item (theo thứ tự `items`) xuất hiện trong phần TRAIN.

    `interactions` (user_id, item_id, timestamp) đã sắp theo (user, thời gian, thứ
    tự dòng); leave-one-out: 2 tương tác cuối mỗi user (valid, test) không đếm."""
    from_end = interactions.groupby("user_id", sort=False).cumcount(ascending=False)
    counts = interactions.loc[from_end >= 2, "item_id"].value_counts()
    return counts.reindex(items).fillna(0).astype(np.int64).to_numpy()
