# Changelog

## 0.6.0 — 2026-09-28

- Thêm cột `title` (required, lấy từ `silver.products`, luôn có sẵn) vào
  schema contract `gold_item_features` — dùng hiển thị tên sản phẩm trên
  `ui/` cùng đợt bổ sung `image_url` (0.5.0). `description` **không** thêm
  vào Gold: cột `description` gốc luôn `NULL` trong toàn bộ dataset (Amazon
  seed không có mô tả text thật, xem `amazone_data/DATA.md`) — UI tự ghép
  "mô tả ngắn" hiển thị từ `category` + `price` đã có sẵn, không thêm cột
  giả.

## 0.5.0 — 2026-09-28

- Thêm cột `image_url` (nullable) vào schema contract `bronze_products`,
  `silver_products`, `gold_item_features` — URL ảnh đại diện sản phẩm, lấy
  từ `amazone_data/prepare_seed_data.py` (trường `images` gốc của Amazon,
  trước đây chỉ dùng để lọc `has_image` rồi bỏ, giờ giữ lại URL thật). Dùng
  để hiển thị ảnh sản phẩm trên `ui/` (Phase 5) — không phải deviation so
  với `de-xuat-trien-khai.md`/`bao-cao-ky-thuat.md` (không có tài liệu nào
  đề cập ảnh sản phẩm), nên không cần ADR, chỉ ghi ở đây +
  `docs/modules/phase-2-lakehouse.md` (addendum). **Yêu cầu schema
  evolution thủ công** cho bảng Iceberg đã tồn tại từ trước (xem addendum
  phase-2 để biết cách áp dụng lên dữ liệu cũ, hoặc cold start lại từ
  `source-db` mới).

## 0.4.0 — 2026-09-27

- Thêm `ranking/gru4rec.py` — di chuyển kiến trúc `GRU4RecNet` +
  `PAD_INDEX` từ `jobs/training/train_sequence.py` sang đây (Phase 5). Lý
  do: MLflow lưu model bằng pickle, `serving/` (image khác, không cài
  `jobs/training/`) cần import được đúng class ở đúng module path để
  `mlflow.pytorch.load_model()` hoạt động — xem
  `docs/modules/phase-5-serving.md`. `torch` **không** thêm vào dependency
  chung của `libs` (chỉ `jobs/training/` và `serving/` tự khai `torch`
  trong `requirements/*.txt` riêng, 2 nơi duy nhất import module này).
  **Hệ quả**: 2 model version train ở Phase 4 (pickle theo path cũ
  `jobs.training.train_sequence.GRU4RecNet`) không load lại được — đã
  train + promote lại sau khi đổi.

## 0.3.0 — 2026-09-27

- Thêm `ranking/base.py` — interface `RankingModel.predict(user_id,
  candidate_items) -> scores` (Phase 4, xem
  `docs/de-xuat-trien-khai.md` Phase 4 / `bao-cao-ky-thuat.md` mục 7.5).
  Không phụ thuộc torch/mlflow/pandas — model-agnostic có chủ đích.
- Thêm `mlflow_utils/registry_client.py` (`get_production_version()`,
  `set_production_version()` — dùng alias MLflow `production`, không dùng
  stage API cũ đã deprecated) và `mlflow_utils/logging_helpers.py`
  (`log_iceberg_snapshot()` — lineage bắt buộc theo mục 7.4). Dùng chung
  giữa `jobs/training/promote.py` (Phase 4) và `model_loader.py` (Phase 5).
- Dependency mới: `mlflow==3.12.0` (khớp version MLflow tracking server đã
  dựng từ Phase 0).

## 0.2.0 — 2026-09-06

- Thêm `iceberg/schema_contracts/gold_user_features.py` — contract cho
  `gold.user_features` (chuỗi item_id gần nhất/user, input cho GRU sequence
  model — xem `docs/decisions/0004-gru-sequence-model-thay-lightgbm.md`).
  Dùng kiểu `ListType` mới trong pyiceberg (chưa dùng ở các contract trước).

## 0.1.0 — 2026-09-05

- Thêm `common/env.py` (đọc biến môi trường bắt buộc/tuỳ chọn).
- Thêm `iceberg/catalog.py` — kết nối Lakekeeper REST Catalog + MinIO dùng
  chung cho mọi job transform.
- Thêm `iceberg/schema_contracts/` cho `bronze.products`,
  `bronze.interactions`, `silver.products`, `silver.interactions`,
  `gold.item_features` (pyiceberg `Schema` + pandera `DataFrameSchema`).
- Thêm `iceberg/validators.py` — enforce schema contract trước khi commit
  Iceberg, raise `SchemaContractViolation` liệt kê toàn bộ dòng lỗi.
- Dependencies: `pyiceberg[pyarrow,s3fs]==0.12.0`, `pandera==0.33.1`,
  `pandas==2.3.3`.

## 0.0.1 — 2026-08-06

- Khởi tạo package dùng chung cho Phase 0.
