# Nguồn: repo thực nghiệm `experiment/` — src/expkit/models/agf_core.py, commit 58d50db (58d50db0f9d21dfc735f0337c3870d67ccff7aa4).
# Chép nguyên theo ADR 0008; chỉ đổi import cho khớp package `reco_mlops_libs`.
# Sửa logic ở repo thực nghiệm trước rồi chép lại, không sửa riêng ở đây.
"""AGF — Adaptive Gated Fusion: biểu diễn item trộn ID với thuộc tính + text.

PyTorch thuần, KHÔNG import RecBole (file này được chép sang `libs/` của
pipeline). Công thức (docs/02-mo-hinh-de-xuat.md):

    f_i = LayerNorm( W_f [ meanpool(E_cat[categories_i]) ; E_store[store_i] ;
                           E_price[price_i] ; W_t · text_i ] )
    π_i = log(1+n_i) / log(1+max_j n_j)          (n_i: tần suất trong TRAIN)
    g_i = σ( W_g [ e_i^id ; f_i ; π_i ] + b_g )
    h_i = g_i ⊙ e_i^id + (1 − g_i) ⊙ f_i

Cờ:
    fusion          "gate" (mặc định) | "sum" (h = e + f) | "id_only" (h = e, không dùng f)
    use_popularity  bỏ π khỏi đầu vào cổng khi False
    use_text        bỏ nhánh text khi False
`output_fusion` (chấm điểm bằng H hay bằng bảng ID) nằm ở wrapper vì nó thuộc
lớp đầu ra của backbone, không thuộc biểu diễn item.

Item lạnh (n_i = 0, chưa có trong train): e^id = 0, π = 0 → h tính từ thuộc tính
(truyền `known=False`). Hàng 0 của mọi bảng là padding và bằng 0.
"""
from __future__ import annotations

import math

import torch
from torch import nn

FUSIONS = ("gate", "sum", "id_only")


def popularity_signal(counts: torch.Tensor) -> torch.Tensor:
    """π = log(1+n) / log(1+max n) ∈ [0, 1]; `counts` đếm trên TRAIN."""
    counts = torch.as_tensor(counts, dtype=torch.float32).clamp(min=0)
    top = float(counts.max()) if counts.numel() else 0.0
    if top <= 0:
        return torch.zeros_like(counts)
    return torch.log1p(counts) / math.log1p(top)


class AGFItemEncoder(nn.Module):
    """Tính h (và g) cho một tập item từ ID + thuộc tính.

    Args:
        n_items: số hàng của bảng ID (hàng 0 = padding).
        d: kích thước biểu diễn.
        n_categories, n_stores, n_prices: kích thước từ điển (token 0 = padding).
        text_dim: kích thước text embedding đầu vào (encoder text đóng băng).
    """

    def __init__(self, n_items: int, d: int, n_categories: int, n_stores: int, n_prices: int,
                 text_dim: int = 384, fusion: str = "gate", use_popularity: bool = True,
                 use_text: bool = True, init_std: float = 0.02, layer_norm_eps: float = 1e-12):
        super().__init__()
        if fusion not in FUSIONS:
            raise ValueError(f"fusion phải thuộc {FUSIONS}, nhận '{fusion}'")
        self.d, self.fusion = d, fusion
        self.use_popularity, self.use_text = bool(use_popularity), bool(use_text)
        self.init_std = init_std

        self.id_emb = nn.Embedding(n_items, d, padding_idx=0)
        if fusion != "id_only":
            self.cat_emb = nn.Embedding(n_categories, d, padding_idx=0)
            self.store_emb = nn.Embedding(n_stores, d, padding_idx=0)
            self.price_emb = nn.Embedding(n_prices, d, padding_idx=0)
            self.text_proj = nn.Linear(text_dim, d) if self.use_text else None
            n_parts = 4 if self.use_text else 3
            self.feat_proj = nn.Linear(n_parts * d, d)
            self.feat_norm = nn.LayerNorm(d, eps=layer_norm_eps)
            if fusion == "gate":
                self.gate = nn.Linear(2 * d + (1 if self.use_popularity else 0), d)
        self.reset_parameters()

    # ------------------------------------------------------------ khởi tạo
    def reset_parameters(self) -> None:
        """Embedding/Linear ~ N(0, init_std), bias = 0 (nên b_g = 0, g ≈ 0,5 lúc đầu),
        LayerNorm = (1, 0), hàng padding = 0."""
        for m in self.modules():
            if isinstance(m, (nn.Linear, nn.Embedding)):
                nn.init.normal_(m.weight, mean=0.0, std=self.init_std)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.zeros_(m.bias)
            if isinstance(m, nn.LayerNorm):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
        self.zero_padding_rows()

    @torch.no_grad()
    def zero_padding_rows(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Embedding) and m.padding_idx is not None:
                m.weight[m.padding_idx].zero_()

    # ------------------------------------------------------------ thành phần
    def id_embeddings(self, item_ids: torch.Tensor, known: torch.Tensor | None = None) -> torch.Tensor:
        """e^id; bằng 0 với item lạnh (`known` False)."""
        e = self.id_emb(item_ids)
        if known is not None:
            e = e * known.to(e.dtype).unsqueeze(-1)
        return e

    def features(self, categories: torch.Tensor, store: torch.Tensor, price: torch.Tensor,
                 text: torch.Tensor | None = None) -> torch.Tensor:
        """f. `categories` [N, L] token (0 = padding) → mean-pool trên token khác 0."""
        if self.fusion == "id_only":
            raise RuntimeError("fusion=id_only không dùng thuộc tính")
        mask = (categories != 0).unsqueeze(-1).to(self.cat_emb.weight.dtype)
        cat = (self.cat_emb(categories) * mask).sum(-2) / mask.sum(-2).clamp(min=1.0)
        parts = [cat, self.store_emb(store), self.price_emb(price)]
        if self.use_text:
            if text is None:
                raise ValueError("use_text=True nhưng không có text")
            parts.append(self.text_proj(text))
        return self.feat_norm(self.feat_proj(torch.cat(parts, dim=-1)))

    def gate_values(self, e_id: torch.Tensor, f: torch.Tensor,
                    popularity: torch.Tensor | None = None) -> torch.Tensor:
        """g = σ(W_g [e ; f ; π] + b_g) ∈ (0,1)^d."""
        parts = [e_id, f]
        if self.use_popularity:
            if popularity is None:
                raise ValueError("use_popularity=True nhưng không có popularity")
            parts.append(popularity.to(e_id.dtype).unsqueeze(-1))
        return torch.sigmoid(self.gate(torch.cat(parts, dim=-1)))

    # ------------------------------------------------------------ h
    def forward(self, item_ids: torch.Tensor, categories: torch.Tensor | None = None,
                store: torch.Tensor | None = None, price: torch.Tensor | None = None,
                text: torch.Tensor | None = None, popularity: torch.Tensor | None = None,
                known: torch.Tensor | None = None, return_gate: bool = False):
        """h cho các item `item_ids` (các tensor thuộc tính cùng số hàng với item_ids).

        Item lạnh: `known=False` (e^id = 0) và `popularity=0`. Với item hoàn toàn
        mới ở lúc phục vụ, truyền item_ids bất kỳ hợp lệ kèm known=False.
        Trả về h, hoặc (h, g) nếu return_gate (g = None khi fusion != gate).
        """
        if self.fusion == "id_only":
            h = self.id_embeddings(item_ids, known)
            return (h, None) if return_gate else h
        e = self.id_embeddings(item_ids, known)
        if popularity is not None and known is not None:
            popularity = popularity * known.to(popularity.dtype)
        f = self.features(categories, store, price, text)
        if self.fusion == "sum":
            h, g = e + f, None
        else:
            g = self.gate_values(e, f, popularity)
            h = g * e + (1.0 - g) * f
        return (h, g) if return_gate else h


# ====================================================================== model tuần tự đầy đủ
# Bản thuần PyTorch của AGF-SASRec / AGF-GRU4Rec cho pipeline (S6). Tên module
# trùng với wrapper RecBole (expkit/models/recbole_agf.py) và lớp của RecBole 1.2.1
# (SASRec, TransformerEncoder, GRU4Rec), nên state_dict nạp chéo được: test S6
# dùng điều này để kiểm tra hai bản cho cùng điểm.

class MultiHeadAttention(nn.Module):
    def __init__(self, n_heads: int, hidden_size: int, hidden_dropout_prob: float, attn_dropout_prob: float,
                 layer_norm_eps: float):
        super().__init__()
        if hidden_size % n_heads != 0:
            raise ValueError(f"hidden_size {hidden_size} không chia hết cho n_heads {n_heads}")
        self.num_attention_heads = n_heads
        self.attention_head_size = hidden_size // n_heads
        self.all_head_size = hidden_size
        self.sqrt_attention_head_size = math.sqrt(self.attention_head_size)
        self.query = nn.Linear(hidden_size, hidden_size)
        self.key = nn.Linear(hidden_size, hidden_size)
        self.value = nn.Linear(hidden_size, hidden_size)
        self.attn_dropout = nn.Dropout(attn_dropout_prob)
        self.dense = nn.Linear(hidden_size, hidden_size)
        self.LayerNorm = nn.LayerNorm(hidden_size, eps=layer_norm_eps)
        self.out_dropout = nn.Dropout(hidden_dropout_prob)

    def _heads(self, x: torch.Tensor) -> torch.Tensor:
        return x.view(x.size()[:-1] + (self.num_attention_heads, self.attention_head_size))

    def forward(self, input_tensor: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        q = self._heads(self.query(input_tensor)).permute(0, 2, 1, 3)
        k = self._heads(self.key(input_tensor)).permute(0, 2, 3, 1)
        v = self._heads(self.value(input_tensor)).permute(0, 2, 1, 3)
        scores = torch.matmul(q, k) / self.sqrt_attention_head_size + attention_mask
        probs = self.attn_dropout(torch.softmax(scores, dim=-1))
        ctx = torch.matmul(probs, v).permute(0, 2, 1, 3).contiguous()
        ctx = ctx.view(ctx.size()[:-2] + (self.all_head_size,))
        return self.LayerNorm(self.out_dropout(self.dense(ctx)) + input_tensor)


class FeedForward(nn.Module):
    def __init__(self, hidden_size: int, inner_size: int, hidden_dropout_prob: float, layer_norm_eps: float):
        super().__init__()
        self.dense_1 = nn.Linear(hidden_size, inner_size)
        self.dense_2 = nn.Linear(inner_size, hidden_size)
        self.LayerNorm = nn.LayerNorm(hidden_size, eps=layer_norm_eps)
        self.dropout = nn.Dropout(hidden_dropout_prob)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.dense_1(x)
        h = h * 0.5 * (1.0 + torch.erf(h / math.sqrt(2.0)))  # gelu (erf) như RecBole
        return self.LayerNorm(self.dropout(self.dense_2(h)) + x)


class TransformerLayer(nn.Module):
    def __init__(self, n_heads, hidden_size, inner_size, hidden_dropout_prob, attn_dropout_prob, layer_norm_eps):
        super().__init__()
        self.multi_head_attention = MultiHeadAttention(n_heads, hidden_size, hidden_dropout_prob,
                                                       attn_dropout_prob, layer_norm_eps)
        self.feed_forward = FeedForward(hidden_size, inner_size, hidden_dropout_prob, layer_norm_eps)

    def forward(self, x, attention_mask):
        return self.feed_forward(self.multi_head_attention(x, attention_mask))


class TransformerEncoder(nn.Module):
    def __init__(self, n_layers, n_heads, hidden_size, inner_size, hidden_dropout_prob, attn_dropout_prob,
                 layer_norm_eps):
        super().__init__()
        self.layer = nn.ModuleList([
            TransformerLayer(n_heads, hidden_size, inner_size, hidden_dropout_prob, attn_dropout_prob, layer_norm_eps)
            for _ in range(n_layers)])

    def forward(self, x, attention_mask):
        for layer in self.layer:
            x = layer(x, attention_mask)
        return x


def causal_attention_mask(item_seq: torch.Tensor) -> torch.Tensor:
    """Mask trái sang phải + che padding (0); cộng vào điểm attention (0 hoặc -10000)."""
    mask = (item_seq != 0).unsqueeze(1).unsqueeze(2)
    mask = torch.tril(mask.expand((-1, -1, item_seq.size(-1), -1)))
    return torch.where(mask, 0.0, -10000.0)


def gather_last(output: torch.Tensor, item_seq_len: torch.Tensor) -> torch.Tensor:
    """Vector ở vị trí item_seq_len - 1 (chuỗi căn trái, padding 0 ở cuối)."""
    idx = (item_seq_len - 1).view(-1, 1, 1).expand(-1, -1, output.shape[-1])
    return output.gather(dim=1, index=idx).squeeze(1)


class AGFSequentialModel(nn.Module):
    """AGF với backbone SASRec (Transformer 2 lớp, 2 head) hoặc GRU4Rec (GRU 1 lớp).

    Chuỗi đầu vào: [B, L] chỉ số item (1..n_items-1), căn trái, 0 = padding;
    item_seq_len: [B]. Thuộc tính item đặt bằng `set_item_features` (buffer
    không lưu trong state_dict; pipeline dựng lại từ features). Lúc phục vụ:
    H = item_tables() tính một lần, rồi điểm = seq_output(seq, H_in) · H_outᵀ.
    """

    def __init__(self, n_items: int, n_categories: int, n_stores: int, n_prices: int, backbone: str = "sasrec",
                 d: int = 64, max_len: int = 50, dropout: float = 0.2, n_layers: int = 2, n_heads: int = 2,
                 inner_size: int = 256, gru_layers: int = 1, text_dim: int = 384, fusion: str = "gate",
                 output_fusion: bool = True, use_popularity: bool = True, use_text: bool = True,
                 layer_norm_eps: float = 1e-12, init_std: float = 0.02):
        super().__init__()
        if backbone not in ("sasrec", "gru4rec"):
            raise ValueError(f"backbone phải là sasrec hoặc gru4rec, nhận '{backbone}'")
        self.backbone, self.n_items, self.max_len, self.d = backbone, n_items, max_len, d
        self.fusion, self.output_fusion = fusion, bool(output_fusion)
        self.init_std = init_std
        if backbone == "sasrec":
            self.position_embedding = nn.Embedding(max_len, d)
            self.trm_encoder = TransformerEncoder(n_layers, n_heads, d, inner_size, dropout, dropout, layer_norm_eps)
            self.LayerNorm = nn.LayerNorm(d, eps=layer_norm_eps)
            self.dropout = nn.Dropout(dropout)
        else:
            self.emb_dropout = nn.Dropout(dropout)
            self.gru_layers = nn.GRU(d, d, num_layers=gru_layers, bias=False, batch_first=True)
            self.dense = nn.Linear(d, d)
        self._init_backbone()
        self.agf = AGFItemEncoder(n_items, d, n_categories, n_stores, n_prices, text_dim=text_dim, fusion=fusion,
                                  use_popularity=use_popularity, use_text=use_text, init_std=init_std,
                                  layer_norm_eps=layer_norm_eps)
        if backbone == "gru4rec":  # như GRU4Rec của RecBole: embedding ID khởi tạo xavier_normal
            nn.init.xavier_normal_(self.agf.id_emb.weight)
            self.agf.zero_padding_rows()
        self.loss_fct = nn.CrossEntropyLoss()

    def _init_backbone(self) -> None:
        """Khởi tạo như RecBole: SASRec N(0, 0.02), bias 0, LayerNorm (1, 0);
        GRU4Rec: xavier_uniform cho trọng số GRU, Linear mặc định của PyTorch."""
        if self.backbone == "sasrec":
            for m in self.modules():
                if isinstance(m, (nn.Linear, nn.Embedding)):
                    nn.init.normal_(m.weight, 0.0, self.init_std)
                if isinstance(m, nn.Linear) and m.bias is not None:
                    nn.init.zeros_(m.bias)
                if isinstance(m, nn.LayerNorm):
                    nn.init.ones_(m.weight)
                    nn.init.zeros_(m.bias)
        else:
            for name, p in self.gru_layers.named_parameters():
                if name.startswith(("weight_hh", "weight_ih")):
                    nn.init.xavier_uniform_(p)

    # ------------------------------------------------------------ thuộc tính item
    def set_item_features(self, categories, store, price, text, train_counts) -> None:
        """Hàng i = item i (hàng 0 = padding). train_counts: số lần xuất hiện trong TRAIN."""
        n = self.n_items
        counts = torch.as_tensor(train_counts, dtype=torch.long)
        assert len(counts) == n and len(categories) == n and len(store) == n and len(price) == n
        dev = self.agf.id_emb.weight.device
        known = counts > 0
        known[0] = False
        self.register_buffer("known", known.to(dev), persistent=False)
        self.register_buffer("popularity", popularity_signal(counts).to(dev), persistent=False)
        cats = torch.as_tensor(categories, dtype=torch.long)
        self.register_buffer("attr_categories", (cats if cats.dim() == 2 else cats[:, None]).to(dev),
                             persistent=False)
        self.register_buffer("attr_store", torch.as_tensor(store, dtype=torch.long).to(dev), persistent=False)
        self.register_buffer("attr_price", torch.as_tensor(price, dtype=torch.long).to(dev), persistent=False)
        if text is not None:
            text = torch.as_tensor(text, dtype=torch.float32)
            assert text.shape[0] == n
            self.register_buffer("text_table", text.to(dev), persistent=False)

    def _encode_all(self, return_gate: bool = False):
        ids = torch.arange(self.n_items, device=self.known.device)
        if self.fusion == "id_only":
            h, g = self.agf(ids, return_gate=True)
        else:
            h, g = self.agf(ids, self.attr_categories, self.attr_store, self.attr_price,
                            text=getattr(self, "text_table", None), popularity=self.popularity,
                            known=self.known, return_gate=True)
        pad = torch.ones_like(h[:, :1])
        pad[0] = 0.0
        h = h * pad
        return (h, g) if return_gate else h

    def item_tables(self) -> tuple[torch.Tensor, torch.Tensor]:
        """(H_in, H_out) cho mọi item; H_out = bảng ID khi output_fusion=False."""
        h = self._encode_all()
        if self.output_fusion or self.fusion == "id_only":
            return h, h
        ids = torch.arange(self.n_items, device=h.device)
        return h, self.agf.id_embeddings(ids, self.known)

    @torch.no_grad()
    def gate_table(self) -> torch.Tensor | None:
        return self._encode_all(return_gate=True)[1]

    # ------------------------------------------------------------ backbone
    def seq_output(self, item_seq: torch.Tensor, item_seq_len: torch.Tensor, h_in: torch.Tensor) -> torch.Tensor:
        if self.backbone == "sasrec":
            pos = torch.arange(item_seq.size(1), dtype=torch.long, device=item_seq.device)
            x = h_in[item_seq] + self.position_embedding(pos.unsqueeze(0).expand_as(item_seq))
            x = self.dropout(self.LayerNorm(x))
            out = self.trm_encoder(x, causal_attention_mask(item_seq))
        else:
            out, _ = self.gru_layers(self.emb_dropout(h_in[item_seq]))
            out = self.dense(out)
        return gather_last(out, item_seq_len)

    def score_with_tables(self, item_seq, item_seq_len, h_in, h_out) -> torch.Tensor:
        return self.seq_output(item_seq, item_seq_len, h_in) @ h_out.T

    def forward(self, item_seq: torch.Tensor, item_seq_len: torch.Tensor) -> torch.Tensor:
        """Điểm cho mọi item [B, n_items] (tính lại H; lúc phục vụ dùng score_with_tables)."""
        h_in, h_out = self.item_tables()
        return self.score_with_tables(item_seq, item_seq_len, h_in, h_out)

    def loss(self, item_seq: torch.Tensor, item_seq_len: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """Cross-entropy trên toàn bộ item."""
        return self.loss_fct(self.forward(item_seq, item_seq_len), target)
