# Phase 0 — Khung project & hạ tầng nền tảng

> Trạng thái: **Hoàn thành** (một phần vượt phạm vi kế hoạch gốc — xem mục
> "Khác biệt so với đề xuất ban đầu"). Đối chiếu với `de-xuat-trien-khai.md`
> mục "Phase 0".

Thư mục code: thư mục gốc repo

---

## 1. Mục tiêu ban đầu

Có một repo chạy được `docker compose up` với các service hạ tầng cốt lõi
(MinIO, Iceberg REST Catalog, Postgres cho source-db và cho MLflow), **chưa
có business logic**.

## 2. Những gì đã triển khai

### 2.1 Khung project

| Hạng mục | File | Ghi chú |
|---|---|---|
| Quản lý dependency dev | `.pre-commit-config.yaml` | black/ruff/isort chạy trước commit |
| Bỏ qua file khi build/commit | `.gitignore`, `.dockerignore` | |
| Lệnh vận hành chuẩn hoá | `Makefile` | `make up`, `down`, `clean`, `ps`, `logs`, `validate`, `test`, `smoke`, `extract`, `install-dev` |
| Biến môi trường mẫu | `.env.example` | Liệt kê đủ biến dùng xuyên suốt: DB, MinIO, Lakekeeper, MLflow |
| Package nội bộ dùng chung | `libs/pyproject.toml`, `libs/src/reco_mlops_libs/` | Khởi tạo rỗng, version `0.0.1`, có `py.typed`; `CHANGELOG.md` ghi mốc khởi tạo (2026-08-06) |

### 2.2 Hạ tầng Docker Compose (`docker-compose.yml`)

| Service | Vai trò | Điểm đáng chú ý |
|---|---|---|
| `source-db` | Postgres — DB nguồn giả lập (OLTP) | Cổng `5433`, có healthcheck `pg_isready`, mount `infra/source-db/init/` để tự chạy `schema.sql` + `seed_data.sql` khi khởi tạo |
| `catalog-db` | Postgres — backend store cho Iceberg REST Catalog | Cổng `5434`, tách biệt hoàn toàn khỏi `source-db` |
| `mlflow-db` | Postgres — backend store cho MLflow | Cổng `5435`, tách biệt khỏi `source-db` và `catalog-db` — đúng nguyên tắc không lẫn "dữ liệu nghiệp vụ giả lập" với "metadata MLOps" |
| `minio` | Object storage (S3-compatible) | Cổng API `9000`, Console `9001` |
| `minio-init` | Job một lần, tạo bucket | Tạo `iceberg-warehouse` và `mlflow-artifacts` qua `infra/minio/init-buckets.sh` |
| `catalog-migrate` + `lakekeeper` | Iceberg REST Catalog | Dùng **Lakekeeper** (`quay.io/lakekeeper/catalog`) thay vì tự viết REST Catalog; `catalog-migrate` chạy schema migration trước, `lakekeeper` mới `serve`. Cổng `8181` |
| `catalog-bootstrap` | Job một lần | Gọi API Lakekeeper qua `curl` để khởi tạo warehouse `reco` trỏ vào bucket MinIO (`infra/iceberg-catalog/bootstrap.sh`) |
| `mlflow` | MLflow Tracking Server | Backend store = `mlflow-db` (Postgres), artifact root = `s3://mlflow-artifacts` (MinIO). Cổng `5000` |
| `extract-job` | Job Phase 1 (profile `jobs`, không chạy cùng `docker compose up` mặc định) | Build từ `infra/docker/extract/Dockerfile` |

Toàn bộ service nằm trên network riêng `reco-net`, dữ liệu bền vững qua 4
named volume (`source_db_data`, `catalog_db_data`, `mlflow_db_data`,
`minio_data`).

`docker-compose.override.yml` đã tồn tại nhưng hiện là placeholder rỗng
(`services: {}`) — dành chỗ cho Prometheus/Grafana ở Phase 7 mà không phải
sửa cấu trúc file sau này.

### 2.3 Script kiểm thử hạ tầng

- `scripts/validate_phase0.py` — kiểm tra cấu trúc file Phase 0, cú pháp
  shell script, và Compose config (khi có Docker) mà không cần khởi động
  service thật.
- `scripts/smoke_test.sh` — kiểm tra các service đã lên (`make smoke`), xác
  nhận seed data đã nạp vào `source-db`.
- `tests/unit/test_package_metadata.py` — xác nhận `reco_mlops_libs` cài đặt
  và import được.

### 2.4 Seed data

- `infra/source-db/init/schema.sql` — 3 bảng nghiệp vụ (`users`, `products`,
  `interactions`, có `created_at`/`updated_at` theo đúng yêu cầu thiết kế) +
  2 bảng metadata pipeline (`pipeline.extraction_watermarks`,
  `pipeline.extraction_runs` — bảng này thực ra được chuẩn bị sẵn từ Phase 0
  để Phase 1 dùng ngay, xem `phase-1-extract-watermark.md`).
- `infra/source-db/init/seed_data.sql` — dữ liệu mẫu ban đầu.

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Đánh giá |
|---|---|---|
| REST Catalog tự cấu hình qua `catalog.env` | Dùng **Lakekeeper** làm REST Catalog server đầy đủ (có DB migration riêng, healthcheck, API bootstrap warehouse) | Vượt kế hoạch — chọn một implementation catalog trưởng thành hơn là tự dựng, giảm rủi ro kỹ thuật cho Phase 2 |
| MLflow infra thuộc Phase 4 | MLflow tracking server + `mlflow-db` đã dựng ngay ở Phase 0 | Làm sớm hơn kế hoạch, không sai nguyên tắc (MLflow không phụ thuộc dữ liệu nghiệp vụ nào), giúp Phase 4 sau này không phải động vào hạ tầng nữa |
| `libs/` chỉ có `pyproject.toml` | Đã có `src/reco_mlops_libs/` với `__init__.py`, `py.typed`, cài đặt được (`pip install -e ./libs`) | Đúng kế hoạch — vẫn là khung rỗng, các module `iceberg/`, `mlflow_utils/` chưa thêm (thuộc Phase 2/4) |

## 4. Definition of Done — đối chiếu

| Tiêu chí (từ `de-xuat-trien-khai.md`) | Trạng thái |
|---|---|
| `docker compose up` chạy được | ✅ |
| MinIO UI truy cập được | ✅ (`localhost:9001`) |
| Kết nối được Postgres source-db, thấy bảng seed sẵn | ✅ |
| Chưa cần Iceberg table nào tồn tại | ✅ đúng phạm vi — Lakekeeper mới chỉ có warehouse rỗng, chưa có bảng Iceberg nào (việc đó thuộc Phase 2) |

**Kết luận: Phase 0 đạt Definition of Done, sẵn sàng cho Phase 1.**

## 5. Việc còn để lại cho phase sau

- `libs/src/reco_mlops_libs/common/` (config.py, logging.py, env.py) — chưa
  có, sẽ cần khi Phase 2 viết `iceberg/catalog.py`.
- `infra/docker/transform|training|serving|ui|monitoring|airflow/Dockerfile`
  — chưa tồn tại, đúng vì các job tương ứng chưa được viết.

## 6. Danh sách file đã triển khai

### Hạ tầng Docker Compose

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `docker-compose.yml` | Định nghĩa toàn bộ service hạ tầng nền (mở rộng thêm ở mỗi phase sau) | Network `reco-net`; service Phase 0: `source-db`, `catalog-db`, `mlflow-db`, `minio`, `minio-init`, `catalog-migrate`, `lakekeeper`, `catalog-bootstrap`, `mlflow` |
| `docker-compose.override.yml` | Placeholder cho Prometheus/Grafana (Phase 7) | `services: {}` — cố ý để trống, load được ngay mà không cần sửa cấu trúc sau này |
| `infra/minio/init-buckets.sh` | Tạo bucket MinIO khi khởi động | Đọc `ICEBERG_BUCKET`, `MLFLOW_ARTIFACT_BUCKET` (mở rộng thêm bucket ở Phase 1/3) |
| `infra/iceberg-catalog/catalog.env` | Biến môi trường cấu hình Lakekeeper | `LAKEKEEPER__ENABLE_DEFAULT_PROJECT`, `LAKEKEEPER__SECRET_BACKEND=postgres` |
| `infra/iceberg-catalog/bootstrap.sh` | Gọi API Lakekeeper tạo warehouse `reco` trỏ MinIO | Idempotent hoá ở Phase 2 (mục 4) — coi 400 `CatalogAlreadyBootstrapped`/`CreateWarehouseStorageProfileOverlap` là thành công khi rerun |
| `infra/source-db/init/schema.sql` | Schema Postgres nguồn: `users`, `products`, `interactions`, `pipeline.extraction_watermarks`, `pipeline.extraction_runs` | Bảng `pipeline.*` chuẩn bị sẵn cho Phase 1 dùng ngay |
| `infra/source-db/init/seed_data.sql` | Dữ liệu seed ban đầu (3 user, 5 product, 8 interaction) | — |
| `infra/docker/mlflow/Dockerfile` | Image MLflow tracking server, build từ image gốc + thêm driver S3/Postgres | Base image `ghcr.io/mlflow/mlflow`, cài thêm `boto3`, `psycopg2-binary` |

### Khung project & package dùng chung

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `Makefile` | Lệnh vận hành chuẩn hoá (mở rộng target ở mỗi phase sau) | Target Phase 0: `up`, `down`, `clean`, `ps`, `logs`, `validate`, `test`, `smoke`, `install-dev` |
| `.env.example` / `.env` | Toàn bộ biến môi trường của dự án (mở rộng ở mỗi phase sau) | `.env` không commit; `.env.example` là nguồn tham chiếu đầy đủ |
| `.pre-commit-config.yaml` | Lint/format trước commit | black/ruff/isort |
| `.gitignore`, `.dockerignore` | Loại trừ file khi commit/build image | `.dockerignore` có exception `!tests/integration` (thêm ở Phase 2) |
| `libs/pyproject.toml` | Định nghĩa package `reco_mlops_libs` cài được (`pip install -e ./libs`) | Version tăng dần theo phase — xem `libs/CHANGELOG.md` |
| `libs/CHANGELOG.md`, `libs/README.md` | Nhật ký thay đổi + mô tả package | Bắt buộc cập nhật mỗi khi sửa `libs/` (CLAUDE.md nguyên tắc #5) |

### Script kiểm thử & test

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `scripts/validate_phase0.py` | Kiểm tra file bắt buộc + cú pháp YAML/shell + `docker compose config` | Không hard-code version `libs/` cụ thể (đã nới ở Phase 2) |
| `scripts/smoke_test.sh` | Kiểm tra service đã lên + seed data đã nạp | Dùng bởi `make smoke` |
| `tests/unit/test_package_metadata.py` | Kiểm tra tên package + version là semver hợp lệ | Không còn cứng version `0.0.1` (đã nới ở Phase 2) |
