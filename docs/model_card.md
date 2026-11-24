# Model card — AGF-GRU4Rec (`reco-mlops-agf`)

> Theo tinh thần [Model Cards for Model Reporting](https://arxiv.org/abs/1810.03993),
> rút gọn cho quy mô MVP khóa luận. Model chính từ
> [ADR 0008](decisions/0008-agf-gru4rec-thay-gru4rec.md); GRU4Rec chỉ-ID cũ
> (ADR 0004) giữ ở phụ lục cuối file.

## Mô tả

Sequence model dự đoán item tiếp theo từ tối đa **10** item gần nhất của user.
Mỗi item có biểu diễn `h_i` trộn embedding ID `e_i` với vector thuộc tính
`f_i` bằng một cổng theo từng chiều có nhận tín hiệu độ phổ biến `π_i`:

```
f_i = LayerNorm(W_f [meanpool(E_cat[categories_i]); E_store[store_i]; E_price[price_i]; W_t·text_i])
π_i = log(1+n_i) / log(1+max_j n_j)        (n_i: tần suất trong phần TRAIN)
g_i = σ(W_g [e_i; f_i; π_i] + b_g),   h_i = g_i ⊙ e_i + (1 − g_i) ⊙ f_i
s_u = GRU(h_{i_1..i_L}),   score(u, i) = s_u · h_i
```

`h` dùng ở **cả** đầu vào lẫn lớp chấm điểm. Item chưa có tương tác train:
`e_i = 0`, `π_i = 0` → chấm từ thuộc tính. Code:
`libs/src/reco_mlops_libs/ranking/agf/` (chép từ repo thực nghiệm, commit
`58d50db`), job `jobs/training/train_agf.py`.

**Cấu hình** (`jobs/training/agf_method_config.yaml`, chọn ở thực nghiệm theo
validation, **không** tune lại trên dữ liệu pipeline): `d = 64`, GRU 1 lớp,
`max_len = 10`, `dropout = 0,2`, Adam lr 1e-3, batch 2048, cross-entropy trên
toàn bộ item, early stopping theo valid NDCG@10 (patience 10, tối đa 200
epoch), seed 2023. Train trên CPU.

## Dữ liệu huấn luyện

Amazon Reviews 2023 `Video_Games` (Nintendo Switch + PlayStation 4 + Xbox
One, top 3000 item nhiều review nhất, k-core 8) — tập seed gồm 14.748 tương
tác, 1.419 user, 682 item (cộng 5 sản phẩm fixture `p001–p005` của
`seed_data.sql` → 687 dòng `gold.item_features`), qua pipeline Phase 1–3:

- Chuỗi: `gold.user_features` (tối đa 10 item gần nhất/user) — cùng nguồn với
  chuỗi serving đọc từ Feast.
- Thuộc tính (`gold.item_features`): cây danh mục `category_path` (tối đa 3
  cấp; rỗng → `category`), `brand` (vai trò store), bucket giá theo quantile,
  text = `title` + danh mục mã hoá bằng `all-MiniLM-L6-v2` (đóng băng). Token
  danh mục/brand xuất hiện ở dưới 5 item → `[UNK]`. Vocab và biên bucket fit
  lại mỗi lần train (artifact `agf/feature_vocab.json`,
  `agf/price_bucket_edges.json`).
- Split: **leave-one-out theo user** — item cuối → test, item kế cuối →
  valid, còn lại → train; user dưới 3 tương tác bị bỏ.
- Không dùng `average_rating`, `rating_number` (thống kê toàn cục, rò rỉ).

## Metric

Mọi metric là full ranking trên toàn bộ item (chỉ che `[PAD]`), log cùng một
run MLflow:

| Metric | Ý nghĩa |
|---|---|
| `val_ndcg_at_10` | Metric chính — dùng để promote (`promote.py`) và theo dõi xu hướng (`model_quality_report.py`) |
| `val_recall_at_10`, `val_*_at_20`, `test_*` | Phụ |
| `baseline_pop_val_*`, `baseline_pop_test_*` | Popularity (đếm trên train) **trên đúng split và cách xếp hạng đó** — mốc tối thiểu model phải vượt |
| `val_*_epoch` (theo epoch), `figures/*.png` | Đường cong huấn luyện và so sánh với Pop, xem ở tab Model metrics / Artifacts của run (từ version 4) |
| `item_table_max_abs_diff` | Sai khác giữa điểm từ `item_table.npy` (serving) và điểm tính trực tiếp — phải < 1e-5 (job tự fail nếu không) |

**Số đo trên dữ liệu pipeline** (lần train đầu sau cold start, 2026-11-19,
CPU, seed mặc định, 1 lần chạy; 7.581 mẫu train, 1.378 mẫu valid; dừng ở
epoch 56, epoch tốt nhất 44, ~35 giây):

| Version | Split | NDCG@10 AGF | NDCG@10 Pop | Recall@10 AGF | Recall@10 Pop | Recall@20 AGF | Recall@20 Pop |
|---|---|---|---|---|---|---|---|
| 1 | valid | 0,0913 | 0,0444 | 0,1756 | 0,0958 | 0,2605 | 0,1611 |
| 1 | test | 0,0795 | 0,0393 | 0,1480 | 0,0871 | 0,2293 | 0,1524 |

`item_table_max_abs_diff = 0,0` (bảng `H` của serving khớp điểm tính trực
tiếp); `n_params = 125.504` (param của run, không phải metric). Với ~1,4K mẫu valid, sai số chuẩn của Recall@10
cỡ ±0,01 và chỉ có 1 seed — đây là số minh hoạ cho demo, không phải kết luận
thống kê. Run MLflow `ee00649754234186b7ea2ba946bfb199`.

Theo dõi qua các version (`model_quality_report`, 2026-11-24, cùng dữ liệu
Video_Games): `val_ndcg_at_10` version 1 = 0,0913; 2 = 0,0913; 3 = 0,0920;
4 = 0,0920 (`production` = 4). Cặp bằng nhau do seed cố định và dữ liệu không
đổi giữa hai lần train; chênh 0,0007 giữa version 1 và 3 nhỏ hơn sai số chuẩn
nêu trên nên không đọc là cải thiện.

Tham chiếu từ thực nghiệm có kiểm soát (không phải dữ liệu pipeline — không
so trực tiếp): AGF-GRU4Rec valid N@10 0,0321 (Scientific) / 0,0543 (Games),
GRU4Rec chỉ-ID 0,0214 / 0,0481, Pop 0,0096 / 0,0117
(`../experiment/results/tables/summary.md`).

## Giới hạn đã biết

- **Dataset pipeline nhỏ** (~0,7K item) — kết quả dao động mạnh giữa các lần
  train; mục tiêu là demo hạ tầng MLOps. Kết quả ở đây **không** dùng để chọn
  phương pháp hay đưa vào paper (phương pháp được chọn ở repo thực nghiệm).
- **Chuỗi train bị cắt 10 item/user** (thiết kế `gold.user_features`) nên số
  mẫu train và tần suất `π` ít hơn dùng toàn bộ lịch sử.
- **Item thêm vào catalog sau lần train** không có trong `item_vocab.json` →
  điểm 0 (serving fallback), cho tới lần retrain kế tiếp. Item đã có trong
  catalog lúc train nhưng chưa có tương tác thì vẫn chấm được (từ thuộc tính).
- **Cổng `g` không phải tỉ lệ đóng góp**: `e` và `f` có độ lớn khác nhau (`f`
  qua LayerNorm); ở thực nghiệm, ablation cho thấy cổng và `π` không đóng góp
  có ý nghĩa thống kê — lợi ích đến từ thuộc tính và việc dùng `h` ở lớp
  chấm điểm.
- Lần train đầu cần mạng để tải MiniLM (~90 MB); `--no-text` bỏ nhánh text.
- Promote chỉ so 1 con số `val_ndcg_at_10` giữa 2 version, chưa kiểm định
  thống kê.

## Sử dụng đúng cách

Chỉ gọi qua `RankingModel.predict(user_id, candidate_items) -> scores`
(`serving/app/core/model_loader.py::ServingRanker`, CLAUDE.md nguyên tắc #7).
Serving chấm bằng `agf/item_table.npy` **của chính run** đã được promote, không
tính lại `H` và không mã hoá text.

---

## Phụ lục — GRU4Rec chỉ-ID (`reco-mlops-gru4rec`, ADR 0004, legacy)

Không còn được serve từ ADR 0008; chạy tay bằng `make train-gru4rec` để so
sánh. Nội dung model card cũ giữ nguyên dưới đây.

> Theo tinh thần [Model Cards for Model Reporting](https://arxiv.org/abs/1810.03993),
> rút gọn cho quy mô MVP khóa luận. Model thật, đã train + serve — không
> phải mô tả kế hoạch.

### Mô tả

Sequence model dự đoán item tiếp theo user sẽ tương tác, dựa trên chuỗi
item gần nhất (tối đa 10). Thay thế LightGBM ở đề xuất ban đầu — xem
[ADR 0004](decisions/0004-gru-sequence-model-thay-lightgbm.md).

**Kiến trúc**: `Embedding(vocab_size, embedding_dim) → GRU(1 layer,
hidden_size) → Linear(hidden_size, embedding_dim)`, weight-tying giữa input
và output embedding (logit = dot product giữa hidden state đã chiếu và
embedding ứng viên) — sampled softmax, không nhân ma trận toàn bộ vocab mỗi
bước. Chi tiết: `libs/src/reco_mlops_libs/ranking/gru4rec.py`,
`jobs/training/train_sequence.py`.

**Siêu tham số mặc định**: `embedding_dim=32`, `hidden_size=64`,
`num_negatives=100` (negative sampling đều, dùng chung cả batch),
`epochs=20`, `batch_size=64`, `lr=1e-3`. Toàn bộ train trên CPU (quy mô dữ
liệu nhỏ, không cần GPU — xem `bao-cao-ky-thuat.md` mục 7.6).

### Dữ liệu huấn luyện

`amazone_data/data_split/train.parquet` → `gold.user_features` (qua
pipeline Phase 1-3) — Amazon Reviews 2023, category
`Cell_Phones_and_Accessories` (McAuley-Lab), sau k-core filter (k=8):

- ~16.222 interaction, ~1.576 user, ~1.560 item.
- Split: **leave-last-item-out** theo user (item cuối → validation, còn lại
  → training) — không phải random split, đúng chuẩn đánh giá sequential
  recommendation.
- Nhãn là `rating` (1-5), không phải clickstream — mọi tương tác trong
  dataset gốc là đánh giá đã hoàn tất, không có tín hiệu "chỉ xem".

### Metric (validation, k=10)

Đo tại `jobs/training/evaluate.py::evaluate_val_set`. 3 version đã train
thật (registry `reco-mlops-gru4rec`):

| Version | `val_ndcg_at_10` |
|---|---|
| 1 | 0.0078 |
| 2 | 0.0049 |
| 3 (production hiện tại) | 0.0045 |

Cũng tính `val_recall_at_10`, `val_map_at_10`, `val_coverage_at_10` (cùng
lời gọi `evaluate_val_set`).

**Đọc số liệu này thế nào**: NDCG tuyệt đối thấp — quy mô dữ liệu nhỏ
(~1.560 item, k-core filter mạnh) khiến bài toán khó, và version 3 thấp hơn
version 1/2 nhưng vẫn được promote vì version 1 (số liệu cũ) đã trở nên
**không load lại được** sau khi `GRU4RecNet` chuyển sang `libs/` (Phase 5,
xem `docs/modules/phase-5-serving.md`) — số liệu 3 version không hoàn toàn
so sánh công bằng (khác class path). Không nên trích số 0.0078 như "model
tốt nhất hiện dùng".

### Giới hạn đã biết

- **Dataset nhỏ, dễ overfit** (`amazone_data/DATA.md` mục 4.2) — phù hợp
  mục tiêu demo hạ tầng MLOps, không phải benchmark chất lượng gợi ý.
- **Cold-start item cao (30.1%)** trong `test.parquet` — model không tự xử
  lý được item chưa từng thấy lúc train; `serving` phải fallback sang
  popularity khi cần (xem `serving/app/core/fallback.py`).
- **Không có prediction log lâu dài** — không đo được model quality drift
  thật theo thời gian phục vụ, chỉ đo được vocab coverage + xu hướng metric
  qua version (xem `docs/modules/phase-7-monitoring.md` mục 3).
- **Negative sampling đều (uniform)**, không theo phân phối tần suất — đơn
  giản, dễ giải thích, nhưng có thể sample negative "quá dễ" so với
  phương pháp phổ biến hơn (popularity-based negative sampling).
- **Chưa gate theo p-value/significance** — `promote.py` chỉ so 1 con số
  `val_ndcg_at_10` giữa 2 version, không kiểm định thống kê.

### Sử dụng đúng cách

Chỉ gọi qua interface `RankingModel.predict(user_id, candidate_items) ->
scores` (`libs/src/reco_mlops_libs/ranking/base.py`) — không gọi thẳng
`model.predict(dataframe)`/`net(...)` ở tầng API (CLAUDE.md nguyên tắc #7).
`serving/app/core/model_loader.py::ServingRanker` là cách triển khai đúng
cho tầng phục vụ.
