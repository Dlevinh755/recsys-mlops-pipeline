# Phase 4 — Training & Model Registry (GRU4Rec)

> Trạng thái: **Hoàn thành**. Đối chiếu với `de-xuat-trien-khai.md` mục
> "Phase 4" **sau khi áp dụng** [ADR 0004](../decisions/0004-gru-sequence-model-thay-lightgbm.md)
> (nội dung Phase 4 gốc — LightGBM — không còn áp dụng, thay bằng nội dung
> Phase 9 gốc: GRU4Rec là model chính). Phụ thuộc: Phase 0 (MLflow, ADR
> 0002), Phase 3 (`gold.user_features`, `gold.item_features`).

Thư mục code: `jobs/training/`,
`libs/src/reco_mlops_libs/ranking/`,
`libs/src/reco_mlops_libs/mlflow_utils/`.

---

## 1. Mục tiêu ban đầu

Theo ADR 0004 (Phase 9 gốc gộp vào Phase 4): có model ranking dựa trên
chuỗi hành vi (GRU4Rec rút gọn), huấn luyện nhanh trên CPU, tracking đầy đủ
trên MLflow (param/metric/artifact/lineage), có gate promotion, và interface
`predict(user_id, candidate_items) -> scores` để tầng serving sau này không
cần biết chi tiết bên trong model.

## 2. Những gì đã triển khai

### 2.1 `libs/src/reco_mlops_libs/ranking/base.py` — interface

`RankingModel` (ABC) với 1 method trừu tượng `predict(self, user_id: str,
candidate_items: list[str]) -> list[float]`. Không phụ thuộc torch/mlflow/
pandas — model-agnostic có chủ đích (báo cáo kỹ thuật mục 7.5). Implementation
cụ thể (`GRU4RecRanker`) sống trong `jobs/training/train_sequence.py`, không
trong `libs/` — khớp `cau-truc-project.md`.

### 2.2 `libs/src/reco_mlops_libs/mlflow_utils/`

- `registry_client.py` — `get_client()` (1 điểm kết nối `MlflowClient` dùng
  chung, đọc `MLFLOW_TRACKING_URI`, cùng pattern
  `iceberg/catalog.py::get_catalog()`), `get_production_version(name)`,
  `set_production_version(name, version)`.
- `logging_helpers.py` — `log_iceberg_snapshot(table, table_identifier)`,
  log `iceberg_snapshot_id`/`iceberg_table` vào MLflow run đang mở (lineage
  bắt buộc theo báo cáo kỹ thuật mục 7.4).
- Dependency mới `mlflow==3.12.0` (khớp version server) thêm vào
  `libs/pyproject.toml`, bump `0.2.0` → `0.3.0`.

### 2.3 `jobs/training/build_sequence_dataset.py`

Đọc trực tiếp `gold.item_features` (toàn bộ catalog → vocab `product_id ->
index`, 1-indexed, index 0 = PAD) và `gold.user_features` (chuỗi item/user).
Tách **leave-last-item-out per user** — chuẩn đánh giá sequence
recommendation, tương đương "tách theo thời gian": item cuối cùng mỗi chuỗi
giữ lại làm validation target, mọi vị trí trước đó sinh 1 cặp
prefix→target cho training. User có `sequence_length < 2` bị bỏ qua
(không đủ để tạo cặp). `build_dataset()`/`load_dataset()` là hàm thuần,
không ghi trạng thái trung gian — `train_sequence.py` và `evaluate.py` gọi
trực tiếp trong cùng process, tính lại rẻ hơn là thêm 1 tầng lưu trữ mới.

### 2.4 `jobs/training/train_sequence.py` — GRU4Rec

Kiến trúc đúng `bao-cao-ky-thuat.md` mục 7.6: `nn.Embedding(vocab_size, 32)`
(dùng chung input + output, weight tying) → `nn.GRU(32, 64, num_layers=1,
batch_first=True)` (lấy hidden state cuối qua `pack_padded_sequence`, đúng
vị trí hợp lệ của từng sequence, không lấy timestep đệm) →
`nn.Linear(64, 32)` chiếu về không gian embedding. Sampled softmax: logits =
dot-product giữa vector đã chiếu và {embedding của target thật} ∪
{embedding của `--num-negatives` item sample ngẫu nhiên đều, dùng chung cho
cả batch} — không tính toán trên toàn vocab.

Log MLflow: toàn bộ hyperparameter, `train_loss`/epoch, `iceberg_snapshot_id`
(qua `logging_helpers`), 4 metric validation (mục 2.5), artifact `model`
(`mlflow.pytorch.log_model`) + `item_vocab.json` (bắt buộc — không có vocab
thì trọng số không map lại được `product_id`), đăng ký version mới qua
`mlflow.register_model()` (chưa gán alias `production`). Trước khi kết thúc
run, tự dựng `GRU4RecRanker(RankingModel)` và gọi `predict()` thật trên 1
user mẫu — tự kiểm tra interface hoạt động trước khi coi run xong.

### 2.5 `jobs/training/evaluate.py`

4 hàm thuần `ndcg_at_k`/`recall_at_k`/`map_at_k`/`coverage_at_k` (rank dựa
trên so sánh điểm số trực tiếp, không phụ thuộc torch/mlflow — test được
bằng unit test thường). `evaluate_val_set()` chấm điểm **toàn bộ vocab**
(~1565 item, đủ rẻ ở quy mô này) cho mỗi validation example, dùng bởi
`train_sequence.py` ngay sau khi train. `main()` (CLI, `--run-id`) tải lại
model + vocab từ MLflow artifact, tính lại đúng bộ metric trên val set hiện
tại — dùng để re-evaluate độc lập một run đã train.

### 2.6 `jobs/training/promote.py` — gate promotion

So `candidate_metric` (`val_ndcg_at_10` của version chỉ định, mặc định
version mới nhất) với `production_metric` hiện tại
(`registry_client.get_production_version()`). Promote (set alias
`production`) nếu **chưa có** production version nào (luôn nhận version đầu
tiên) hoặc `candidate_metric >= production_metric` — champion/challenger,
không hard-code một ngưỡng tuyệt đối tuỳ tiện.

**Lưu ý dùng alias thay stage**: `de-xuat-trien-khai.md`/`bao-cao-ky-thuat.md`
dùng chữ "trạng thái production" — bản triển khai dùng MLflow **alias**
(`set_registered_model_alias`/`get_model_version_by_alias`) thay vì stage
cũ (Staging/Production/Archived), vì stage đã bị deprecated trong MLflow
hiện đại (server ở đây pin `v3.12.0`). Đây là dùng đúng API hiện hành, không
phải lệch kiến trúc — không có quyết định gốc nào chỉ định cụ thể cơ chế
API nên không cần ADR riêng.

### 2.7 Hạ tầng

- `infra/docker/training/Dockerfile` — cùng pattern `transform`/
  `materialize`, nhưng tách `torch` ra 1 bước `pip install` riêng với
  `--index-url https://download.pytorch.org/whl/cpu` (không trộn với
  `pip install -e /app/libs` — mixing index có thể khiến pip tra cứu nhầm
  index cho `mlflow`/`pyiceberg`/`pandas`, vì index CPU-only của PyTorch chỉ
  host `torch`/`torchvision`/`torchaudio`).
- `requirements/training.txt` — chỉ `torch==2.9.0` (mlflow đến từ
  `libs/pyproject.toml`, theo đúng convention "chỉ liệt kê dependency không
  đến từ `-e ../libs`").
- `docker-compose.yml` — service `training-job` (`profiles: [jobs]`,
  `depends_on: catalog-bootstrap` + `mlflow: service_healthy`).
- `.env`/`.env.example` — thêm `MLFLOW_TRACKING_URI=http://mlflow:5000`
  (biến này trước đó chỉ nằm trong `infra/mlflow/mlflow.env`, chưa job nào
  thật sự đọc — Phase 4 là job đầu tiên dùng).
- `Makefile` — `train-dataset`, `train`, `evaluate` (`ARGS="--run-id <id>"`),
  `promote`.

### 2.8 Test

- `tests/unit/training/test_build_sequence_dataset.py` — leave-last-out với
  chuỗi độ dài 1/2/3/10, dùng `_FakeTable` giả lập `.scan().to_arrow()` (không
  cần Iceberg thật).
- `tests/unit/training/test_evaluate.py` — 4 hàm metric, so khớp giá trị
  tính tay (rank 1/2/ngoài-k, coverage).

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Đánh giá |
|---|---|---|
| Phase 4 = LightGBMRanker + `negative_sampling.py` | GRU4Rec, không có `negative_sampling.py` riêng (sample âm inline trong `train_sequence.py`) | Theo ADR 0004, không phải lệch mới |
| "Registry có version ở trạng thái production" | Dùng MLflow alias `production`, không dùng stage cũ | Stage đã deprecated ở MLflow hiện đại; không lệch kiến trúc, chỉ khác API cụ thể (mục 2.6) |
| Không nêu rõ cách tách train/val | Leave-last-item-out per user (chuẩn sequence rec) | Tương đương "tách theo thời gian" vì tách theo vị trí cuối chuỗi = mới nhất; đơn giản, không cần đụng lại Phase 1/2 để nạp thêm `test.parquet` |
| `jobs/candidates/similar_items.py`, `popular_items.py` (Phase 4 gốc, LightGBM) | Không làm ở Phase 4 | Không nằm trong Phase 9 gốc (nội dung GRU4Rec thay thế Phase 4) theo ADR 0004; đây là candidate generation cho serving — để Phase 5 |

## 4. Definition of Done — đối chiếu

Chạy thật trên MLflow + Lakekeeper + MinIO (không mock):

| Tiêu chí (từ `de-xuat-trien-khai.md`, đã điều chỉnh theo ADR 0004) | Trạng thái | Bằng chứng |
|---|---|---|
| Chuỗi build_sequence_dataset → train → evaluate → promote chạy được ngoài Airflow | ✅ | `make train-dataset`/`train`/`evaluate`/`promote` (`docker compose --profile jobs run`) |
| Experiment + model artifact + `iceberg_snapshot_id` xuất hiện trên MLflow | ✅ | Experiment `reco-mlops-gru4rec`, run `959046d0...`; param `iceberg_snapshot_id`/`iceberg_table`; artifact `model/` + `item_vocab.json` |
| Registry có version ở trạng thái "production" | ✅ (qua alias, xem mục 2.6) | Version 1 gán alias `production`; `get_production_version()` trả đúng version 1, `aliases=['production']` |
| Gate promotion theo metric, không hard-code ngưỡng tuỳ tiện | ✅ | Train version 2 (5 epoch, yếu hơn) → `promote` trả `promoted: false` vì `candidate_metric(0.0049) < production_metric(0.0078)` |
| `train_sequence.py` trả về object implement đúng `predict(user_id, candidate_items) -> scores` | ✅ | `GRU4RecRanker(RankingModel)`, tự gọi `predict()` trong run làm self-check trước khi log model |
| `evaluate.py` — NDCG/Recall/MAP/Coverage | ✅ | `val_ndcg_at_10`, `val_recall_at_10`, `val_map_at_10`, `val_coverage_at_10`; CLI `evaluate --run-id <id>` tái tính khớp 100% số đã log lúc train |

**Kết luận: Phase 4 đạt Definition of Done, sẵn sàng cho Phase 5 (Serving).**

Ghi chú số liệu thật (tham khảo, không phải tiêu chí Done): NDCG@10≈0.0078,
Recall@10≈0.014, MAP@10≈0.0059, Coverage@10≈0.79 sau 20 epoch trên 9798 ví dụ
train / 1567 ví dụ validation, vocab 1566 (1565 sản phẩm + PAD). Số liệu
thấp vì dữ liệu ít (dataset nhỏ, tương tác thưa) — phù hợp dự đoán ở quy mô
MVP, không phải lỗi triển khai; sẽ cải thiện tự nhiên khi có thêm dữ liệu
thật (`test.parquet`/`incoming/` dành cho việc này ở Phase 4/7 sau, xem mục
5).

## 5. Việc còn để lại cho phase sau

- ~~Phase 5 (Serving) phải tự quyết định cách `model_loader.py` tái tạo lại
  một `GRU4RecRanker`/`RankingModel`...~~ — **đã giải quyết ở Phase 5**:
  `serving/app/core/model_loader.py::ServingRanker` implement lại
  `RankingModel` (không dùng `mlflow.pyfunc`), lấy chuỗi từ Feast+Redis
  thay vì scan Iceberg. Yêu cầu 1 thay đổi ngược lại Phase 4:
  `GRU4RecNet` phải chuyển từ `jobs/training/train_sequence.py` sang
  `libs/src/reco_mlops_libs/ranking/gru4rec.py` để `serving` (image khác)
  unpickle được — xem `docs/modules/phase-5-serving.md` mục 2.2. Đã train +
  promote lại model sau khi chuyển (2 version cũ pickle theo path cũ không
  load lại được).
- ~~Cold-start fallback thật...~~ — **đã giải quyết ở Phase 5**:
  `serving/app/core/fallback.py` dùng `gold.item_features` làm popularity
  fallback, đúng như dự kiến.
- **`amazone_data/data_split/test.parquet`/`incoming/`** vẫn chưa nạp vào
  pipeline (quyết định giữ nguyên từ Phase 1) — dành cho demo drift/retrain
  ở Phase 4 mở rộng hoặc Phase 7, không phải việc của Phase 4 cơ bản.
- **`jobs/candidates/similar_items.py`, `popular_items.py`** — candidate
  generation cho serving, chưa làm (xem mục 3).
- Số liệu metric còn thấp (mục 4) — có thể cải thiện bằng nhiều dữ liệu
  hơn, tune hyperparameter, hoặc tăng `--num-negatives`/`--epochs`; không
  chặn Phase 5 vì Definition of Done không đặt ngưỡng chất lượng tuyệt đối,
  chỉ yêu cầu quy trình train→evaluate→promote chạy đúng.

## 6. Danh sách file đã triển khai

### `libs/` (version 0.3.0)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `libs/src/reco_mlops_libs/ranking/base.py` | Interface `RankingModel.predict()` | ABC, không phụ thuộc torch/mlflow |
| `libs/src/reco_mlops_libs/mlflow_utils/registry_client.py` | `get_client()`, `get_production_version()`, `set_production_version()` | Dùng alias MLflow `production`, không dùng stage cũ |
| `libs/src/reco_mlops_libs/mlflow_utils/logging_helpers.py` | `log_iceberg_snapshot()` | Log `iceberg_snapshot_id`/`iceberg_table` |

### `jobs/training/` (image `training-job`)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/training/build_sequence_dataset.py` | Vocab + leave-last-item-out split từ Gold | `PAD_INDEX=0`; `load_dataset()` dùng chung bởi train/evaluate |
| `jobs/training/train_sequence.py` | `GRU4RecNet`, training loop sampled softmax, `GRU4RecRanker`, log MLflow | `EXPERIMENT_NAME`/`REGISTERED_MODEL_NAME = "reco-mlops-gru4rec"`; CLI `--epochs/--batch-size/--lr/--embedding-dim/--hidden-size/--num-negatives` |
| `jobs/training/evaluate.py` | 4 hàm metric thuần + `evaluate_val_set()` + CLI re-evaluate | CLI: `--run-id`, `--k` (mặc định 10) |
| `jobs/training/promote.py` | Gate promotion champion/challenger | CLI: `--model-name`, `--version` (mặc định version mới nhất); metric gate `val_ndcg_at_10` |

### Hạ tầng & test

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `infra/docker/training/Dockerfile` | Image job training | `torch` cài riêng qua `--index-url` CPU-only, tách khỏi `pip install -e /app/libs` |
| `requirements/training.txt` | Dependency riêng cho image training | `torch==2.9.0` (CPU) |
| `docker-compose.yml` (service `training-job`) | Chạy job training qua Compose profile `jobs` | `depends_on: catalog-bootstrap`, `mlflow: service_healthy` |
| `tests/unit/training/test_build_sequence_dataset.py` | Unit test split leave-last-out | Chạy bằng `make test` (trong container có `pyarrow`) |
| `tests/unit/training/test_evaluate.py` | Unit test 4 hàm metric | Chạy bằng `make test` |
