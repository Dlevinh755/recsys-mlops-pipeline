# Nguồn: repo thực nghiệm `experiment/` — src/expkit/handoff/trainer.py, commit 58d50db (58d50db0f9d21dfc735f0337c3870d67ccff7aa4).
# Chép nguyên theo ADR 0008; chỉ đổi import cho khớp package `reco_mlops_libs`.
# Sửa logic ở repo thực nghiệm trước rồi chép lại, không sửa riêng ở đây.
"""Vòng train AGF thuần PyTorch (bàn giao sang pipeline; docs/03-tich-hop-mlops.md).

KHÔNG import RecBole hay phần còn lại của expkit ngoài `agf_core` (khi chép sang
`libs/`, đổi import của agf_core và features cho khớp). Tái tạo đúng giao thức
của thực nghiệm (thiết kế mục 3.4, 5):

- Chuỗi: sắp theo (user, timestamp, thứ tự dòng); leave-one-out: item cuối → test,
  kế cuối → valid, còn lại → train. Mỗi vị trí t ≥ 1 trong phần train là một mẫu
  (tiền tố → item t), tiền tố giữ tối đa `max_len` item gần nhất, căn trái.
- Loss CE trên toàn bộ item; Adam lr 1e-3, weight_decay 0; batch 2048.
- Đánh giá full ranking trên toàn bộ item, chỉ che [PAD] — như RecBole với model
  tuần tự (không che item đã có trong lịch sử; dữ liệu đã bỏ trùng); NDCG/Recall
  @10, @20; xếp hạng bằng torch.topk.
- Early stopping theo NDCG@10 valid: dừng khi 11 lần đánh giá liên tiếp không
  ≥ giá trị tốt nhất (patience 10, như RecBole); giữ trọng số tốt nhất.

    python -m reco_mlops_libs.ranking.agf.trainer --inter <ds>.inter --meta meta.jsonl \
        --config configs/handoff/method_config.yaml --seed 2023 --out <thư mục artifact>

Artifact (do job train của pipeline sinh, không lấy từ thực nghiệm): model_state.pt,
item_table.npy (H), item_vocab.json, feature_vocab.json, price_bucket_edges.json,
metrics.json.
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from reco_mlops_libs.ranking.agf import features as F
from reco_mlops_libs.ranking.agf.agf_core import AGFSequentialModel

PAD = "[PAD]"
TOPK = (10, 20)


# ---------------------------------------------------------------- cấu hình
@dataclass
class MethodConfig:
    backbone: str = "sasrec"        # sasrec | gru4rec
    d: int = 64
    max_len: int = 50
    dropout: float = 0.2
    n_layers: int = 2
    n_heads: int = 2
    inner_size: int = 256
    gru_layers: int = 1
    fusion: str = "gate"
    output_fusion: bool = True
    use_popularity: bool = True
    use_text: bool = True
    lr: float = 1e-3
    weight_decay: float = 0.0
    batch_size: int = 2048
    eval_batch_size: int = 2048
    epochs: int = 200
    patience: int = 10

    @classmethod
    def from_yaml(cls, path: Path) -> "MethodConfig":
        import yaml

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw = raw.get("method", raw)
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise KeyError(f"method_config có khoá lạ: {sorted(unknown)}")
        return cls(**raw)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


# ---------------------------------------------------------------- dữ liệu
def sort_interactions(inter: pd.DataFrame) -> pd.DataFrame:
    """Sắp theo (user, timestamp, thứ tự dòng gốc), ổn định."""
    inter = inter.reset_index(drop=True)
    inter = inter.assign(_row=np.arange(len(inter)))
    return inter.sort_values(["user_id", "timestamp", "_row"], kind="stable").drop(columns="_row") \
        .reset_index(drop=True)


@dataclass
class Samples:
    """Mẫu (tiền tố → item đích). seq [n, L] căn trái, 0 = padding."""
    seq: np.ndarray
    seq_len: np.ndarray
    target: np.ndarray
    user: np.ndarray            # chỉ số user (theo thứ tự xuất hiện)

    def __len__(self) -> int:
        return len(self.target)


def build_samples(inter: pd.DataFrame, item_index: dict[str, int], max_len: int) -> dict[str, Samples]:
    """inter đã sort_interactions. Trả về {"train", "valid", "test"}; user < 3 tương tác bị bỏ."""
    mapped = inter["item_id"].map(item_index)
    if mapped.isna().any():
        raise KeyError("có item trong tương tác không có trong item_index")
    items = mapped.to_numpy(dtype=np.int64)
    users = inter["user_id"].to_numpy()
    bounds = np.flatnonzero(np.r_[True, users[1:] != users[:-1], True])
    out = {k: ([], [], [], []) for k in ("train", "valid", "test")}
    for u, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        s = items[a:b].astype(np.int64)
        n = len(s)
        if n < 3:
            continue
        for t in range(1, n):
            split = "train" if t < n - 2 else ("valid" if t == n - 2 else "test")
            prefix = s[max(0, t - max_len):t]
            seq, lens, tgt, usr = out[split]
            row = np.zeros(max_len, dtype=np.int64)
            row[:len(prefix)] = prefix
            seq.append(row)
            lens.append(len(prefix))
            tgt.append(s[t])
            usr.append(u)
    return {k: Samples(np.stack(seq), np.asarray(lens), np.asarray(tgt), np.asarray(usr))
            for k, (seq, lens, tgt, usr) in out.items()}


# ---------------------------------------------------------------- đánh giá
@torch.no_grad()
def full_ranks(scores: torch.Tensor, target: torch.Tensor, k: int = max(TOPK)) -> torch.Tensor:
    """Rank (từ 1) của item đích nếu nằm trong top-k, ngược lại k+1 (như RecBole: torch.topk)."""
    top = torch.topk(scores, k, dim=-1).indices
    hit = top == target[:, None]
    return torch.where(hit.any(1), hit.int().argmax(1) + 1, torch.full_like(target, k + 1))


@torch.no_grad()
def evaluate(model: AGFSequentialModel, smp: Samples, batch_size: int = 2048,
             device: str | torch.device = "cpu") -> dict[str, float]:
    model.eval()
    h_in, h_out = model.item_tables()
    ranks = []
    for a in range(0, len(smp), batch_size):
        b = min(a + batch_size, len(smp))
        seq = torch.as_tensor(smp.seq[a:b], device=device)
        lens = torch.as_tensor(smp.seq_len[a:b], device=device)
        tgt = torch.as_tensor(smp.target[a:b], device=device)
        scores = model.score_with_tables(seq, lens, h_in, h_out)
        scores[:, 0] = -np.inf  # chỉ che [PAD], như RecBole với model tuần tự
        ranks.append(full_ranks(scores, tgt).cpu().numpy())
    r = np.concatenate(ranks).astype(float)
    out = {}
    for k in TOPK:
        hit = r <= k
        out[f"recall@{k}"] = float(hit.mean())
        out[f"ndcg@{k}"] = float(np.where(hit, 1.0 / np.log2(r + 1.0), 0.0).mean())
    return out


# ---------------------------------------------------------------- train
@dataclass
class ItemInputs:
    """Đầu vào item theo chỉ số (hàng 0 = [PAD])."""
    tokens: list[str]                  # tokens[i] = item_id của chỉ số i (tokens[0] = [PAD])
    categories: np.ndarray
    store: np.ndarray
    price: np.ndarray
    text: np.ndarray | None
    train_counts: np.ndarray
    vocab: dict
    price_edges: list[float]


def prepare_items(inter: pd.DataFrame, meta: pd.DataFrame, use_text: bool = True,
                  text: np.ndarray | None = None, text_device: str | None = None) -> ItemInputs:
    """Fit lại vocab/biên bucket trên dữ liệu này; item theo thứ tự xuất hiện đầu tiên trong `inter`.
    `text` (theo thứ tự item đó) truyền vào để khỏi encode lại; None → encode bằng features.encode_texts."""
    inter = sort_interactions(inter)
    items = list(dict.fromkeys(inter["item_id"]))
    feats, edges = F.build_item_features(meta, items)
    vocab = F.fit_vocab(feats)
    enc = F.encode_features(feats, vocab)
    if use_text and text is None:
        text = F.encode_texts(F.build_texts(meta, items), device=text_device)

    def pad(a):
        return np.concatenate([np.zeros((1,) + a.shape[1:], dtype=a.dtype), a])

    return ItemInputs([PAD, *items], pad(enc["categories"]), pad(enc["store"]), pad(enc["price"]),
                      pad(text.astype(np.float32)) if use_text else None,
                      pad(F.train_counts(inter, items)), vocab, edges)


def build_model(cfg: MethodConfig, items: ItemInputs) -> AGFSequentialModel:
    model = AGFSequentialModel(
        len(items.tokens), len(items.vocab["categories"]), len(items.vocab["store"]),
        len(items.vocab["price_bucket"]), backbone=cfg.backbone, d=cfg.d, max_len=cfg.max_len,
        dropout=cfg.dropout, n_layers=cfg.n_layers, n_heads=cfg.n_heads, inner_size=cfg.inner_size,
        gru_layers=cfg.gru_layers, text_dim=items.text.shape[1] if items.text is not None else 384,
        fusion=cfg.fusion, output_fusion=cfg.output_fusion, use_popularity=cfg.use_popularity,
        use_text=cfg.use_text)
    model.set_item_features(items.categories, items.store, items.price, items.text, items.train_counts)
    return model


def train(cfg: MethodConfig, inter: pd.DataFrame, items: ItemInputs, seed: int,
          device: str | None = None, log=print) -> dict:
    """Train + early stopping; trả về {"model", "samples", "valid", "test", "best_epoch", ...}."""
    device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    set_seed(seed)
    inter = sort_interactions(inter)
    index = {tok: i for i, tok in enumerate(items.tokens) if i > 0}
    samples = build_samples(inter, index, cfg.max_len)
    model = build_model(cfg, items).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    tr = samples["train"]
    seq_all = torch.as_tensor(tr.seq, device=device)
    len_all = torch.as_tensor(tr.seq_len, device=device)
    tgt_all = torch.as_tensor(tr.target, device=device)
    gen = torch.Generator(device="cpu").manual_seed(seed)

    best, best_epoch, best_state, bad = -1.0, -1, None, 0
    history = []
    t0 = time.time()
    for epoch in range(cfg.epochs):
        model.train()
        perm = torch.randperm(len(tr), generator=gen).to(device)
        total = 0.0
        for a in range(0, len(tr), cfg.batch_size):
            idx = perm[a:a + cfg.batch_size]
            opt.zero_grad()
            loss = model.loss(seq_all[idx], len_all[idx], tgt_all[idx])
            loss.backward()
            opt.step()
            total += loss.item()
        valid = evaluate(model, samples["valid"], cfg.eval_batch_size, device)
        history.append({"epoch": epoch, "loss": total, **{f"valid_{k}": v for k, v in valid.items()}})
        log(f"epoch {epoch}: loss {total:.2f}, valid ndcg@10 {valid['ndcg@10']:.6f}")
        if valid["ndcg@10"] >= best:  # như early_stopping của RecBole (>=, dừng khi > patience)
            best, best_epoch, bad = valid["ndcg@10"], epoch, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad > cfg.patience:
                break
    train_s = time.time() - t0
    model.load_state_dict(best_state)
    valid = evaluate(model, samples["valid"], cfg.eval_batch_size, device)
    test = evaluate(model, samples["test"], cfg.eval_batch_size, device)
    return {"model": model, "samples": samples, "valid": valid, "test": test, "best_epoch": best_epoch,
            "epochs_run": len(history), "history": history, "train_s": train_s, "device": str(device)}


# ---------------------------------------------------------------- xuất artifact
@torch.no_grad()
def table_consistency(model: AGFSequentialModel, item_table: np.ndarray, smp: Samples, n: int = 2048,
                      device="cpu") -> float:
    """max |điểm từ item_table.npy − điểm tính trực tiếp từ model| trên n mẫu."""
    model.eval()
    seq = torch.as_tensor(smp.seq[:n], device=device)
    lens = torch.as_tensor(smp.seq_len[:n], device=device)
    direct = model(seq, lens)
    h = torch.as_tensor(item_table, device=device)
    served = model.score_with_tables(seq, lens, h, h)
    return float((direct - served).abs().max())


def export(result: dict, cfg: MethodConfig, items: ItemInputs, out: Path, seed: int, extra: dict | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    model = result["model"]
    if not (cfg.output_fusion or cfg.fusion == "id_only"):
        raise NotImplementedError("xuất item_table.npy chỉ hỗ trợ output_fusion=True (H dùng chung vào/ra)")
    model.eval()
    with torch.no_grad():
        h = model.item_tables()[0].float().cpu().numpy()
    np.save(out / "item_table.npy", h)
    h_loaded = np.load(out / "item_table.npy")
    diff = table_consistency(model, h_loaded, result["samples"]["valid"], device=result["device"])
    torch.save({"state_dict": model.state_dict(), "method_config": asdict(cfg)}, out / "model_state.pt")
    (out / "item_vocab.json").write_text(json.dumps(items.tokens), encoding="utf-8")
    (out / "feature_vocab.json").write_text(json.dumps(items.vocab), encoding="utf-8")
    (out / "price_bucket_edges.json").write_text(json.dumps(items.price_edges), encoding="utf-8")
    metrics = {"seed": seed, "valid": result["valid"], "test": result["test"], "best_epoch": result["best_epoch"],
               "epochs_run": result["epochs_run"], "train_s": result["train_s"],
               "n_params": int(sum(p.numel() for p in model.parameters())),
               "item_table_max_abs_diff": diff, "method_config": asdict(cfg),
               "env": {"python": platform.python_version(), "torch": torch.__version__, "device": result["device"],
                       "cuda": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None},
               "history": result["history"], **(extra or {})}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


# ---------------------------------------------------------------- CLI
def read_atomic_inter(path: Path) -> pd.DataFrame:
    """File .inter của RecBole (user_id:token, item_id:token, timestamp:float)."""
    df = pd.read_csv(path, sep="\t", dtype=str)
    df.columns = [c.split(":")[0] for c in df.columns]
    return df.assign(timestamp=df["timestamp"].astype(float))[["user_id", "item_id", "timestamp"]]


def read_meta_jsonl(path: Path, items: set[str]) -> pd.DataFrame:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("parent_asin") in items:
                rows.append({k: rec.get(k) for k in F.META_FIELDS})
    return pd.DataFrame(rows, columns=list(F.META_FIELDS))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inter", type=Path, required=True)
    ap.add_argument("--meta", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default=None)
    args = ap.parse_args(argv)
    cfg = MethodConfig.from_yaml(args.config)
    inter = read_atomic_inter(args.inter)
    meta = read_meta_jsonl(args.meta, set(inter["item_id"]))
    items = prepare_items(inter, meta, cfg.use_text, text_device=args.device)
    result = train(cfg, inter, items, args.seed, args.device)
    m = export(result, cfg, items, args.out, args.seed)
    print(json.dumps({k: m[k] for k in ("valid", "test", "best_epoch", "train_s", "item_table_max_abs_diff")},
                     indent=2))


if __name__ == "__main__":
    main()
