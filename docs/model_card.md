# Model card — GRU4Rec (`reco-mlops-gru4rec`)

> Theo tinh thần [Model Cards for Model Reporting](https://arxiv.org/abs/1810.03993),
> rút gọn cho quy mô MVP khóa luận. Model thật, đã train + serve — không
> phải mô tả kế hoạch.

## Mô tả

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

## Dữ liệu huấn luyện

`amazone_data/data_split/train.parquet` → `gold.user_features` (qua
pipeline Phase 1-3) — Amazon Reviews 2023, category
`Cell_Phones_and_Accessories` (McAuley-Lab), sau k-core filter (k=8):

- ~16.222 interaction, ~1.576 user, ~1.560 item.
- Split: **leave-last-item-out** theo user (item cuối → validation, còn lại
  → training) — không phải random split, đúng chuẩn đánh giá sequential
  recommendation.
- Nhãn là `rating` (1-5), không phải clickstream — mọi tương tác trong
  dataset gốc là đánh giá đã hoàn tất, không có tín hiệu "chỉ xem".

## Metric (validation, k=10)

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

## Giới hạn đã biết

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

## Sử dụng đúng cách

Chỉ gọi qua interface `RankingModel.predict(user_id, candidate_items) ->
scores` (`libs/src/reco_mlops_libs/ranking/base.py`) — không gọi thẳng
`model.predict(dataframe)`/`net(...)` ở tầng API (CLAUDE.md nguyên tắc #7).
`serving/app/core/model_loader.py::ServingRanker` là cách triển khai đúng
cho tầng phục vụ.
