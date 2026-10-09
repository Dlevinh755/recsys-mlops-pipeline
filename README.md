# Recommendation MLOps

MVP local cho pipeline gợi ý sản phẩm:

`source-db -> Iceberg/MinIO -> Feast/Redis -> GRU4Rec/MLflow -> FastAPI -> monitoring`

Model ranking chính là GRU4Rec (sequence model), không phải LightGBM như đề
xuất ban đầu — xem
[`docs/decisions/0004-gru-sequence-model-thay-lightgbm.md`](docs/decisions/0004-gru-sequence-model-thay-lightgbm.md).

Trạng thái từng phase: xem [`docs/STATUS.md`](docs/STATUS.md). Tóm tắt
nhanh: Phase 0–3 (hạ tầng, extract, lakehouse Iceberg, feature store
Feast/Redis) đã xong; Phase 4 (training GRU4Rec) đang tới.

## Yêu cầu

- Docker Engine hoặc Docker Desktop có Docker Compose v2
- GNU Make (không bắt buộc; có thể chạy trực tiếp `docker compose`)
- Python 3.11+ cho công cụ phát triển

## Khởi động Phase 0

```bash
cp .env.example .env
make up
make ps
make smoke
```

Các cổng local:

| Service | URL / port |
| --- | --- |
| MinIO API | `http://localhost:9000` |
| MinIO Console | `http://localhost:9001` |
| Lakekeeper UI/API | `http://localhost:8181` |
| MLflow UI | `http://localhost:5000` |
| Source PostgreSQL | `localhost:5433` |
| Catalog PostgreSQL | `localhost:5434` |
| MLflow PostgreSQL | `localhost:5435` |
| Redis (online feature store) | `localhost:6379` |

Tài khoản trong `.env.example` chỉ dành cho local development. Hãy đổi toàn bộ
secret trước khi dùng ngoài máy cá nhân và không commit file `.env`.

## Kiểm tra

```bash
make validate
make test
make smoke
```

- `validate`: kiểm tra cấu trúc Phase 0, shell script và Compose (nếu có Docker).
- `test`: chạy unit test Python.
- `smoke`: kiểm tra service sau khi Compose đã khởi động.

## Chạy Phase 1 incremental extract

Sau khi các service Phase 0 healthy:

```bash
bash scripts/run_job_locally.sh extract
```

Job đọc `products` và `interactions` bằng cursor kép
`(updated_at, primary_key)`. PostgreSQL watermark vẫn là nguồn state duy nhất;
`dlt` đảm nhiệm schema normalization và load Parquet **thẳng lên MinIO**
(bucket `EXTRACT_STAGING_BUCKET`, mặc định `extract-staging`) — không còn
staging ra đĩa cục bộ, xem
`docs/decisions/0003-extract-writes-directly-to-minio.md`.
Chạy lại khi nguồn không đổi phải trả về zero rows và không tạo batch mới.

Mỗi batch dùng một `dlt` dataset xác định từ cursor đầu/cuối và
`write_disposition="replace"`. Vì vậy nếu job lỗi sau khi load nhưng trước khi
watermark commit, retry thay đúng batch cũ thay vì append dữ liệu trùng. State
local của `dlt` được giữ tại `data/dlt-pipelines/`.
Anonymous telemetry của `dlt` được tắt trong Compose cho môi trường local.

## Dữ liệu Amazon thật trong source-db

`infra/source-db/init/seed_data_amazon.sql` là snapshot ~16K interaction/1.6K
sản phẩm từ `amazone_data/data_split/` (xem `amazone_data/DATA.md`) — bổ sung
cho fixture nhỏ trong `seed_data.sql`, đủ lớn cho Phase 3/4. File này được
Postgres **tự động chạy** cùng `seed_data.sql` ngay lần đầu volume
`source_db_data` được tạo (cơ chế `docker-entrypoint-initdb.d` chuẩn của
image `postgres`) — `make up` trên máy mới, hoặc sau `make clean`, đã có sẵn
data, không cần chạy thêm gì.

Nếu đổi logic mapping (`scripts/seed_source_db.py`) hoặc đổi dữ liệu nguồn
(`amazone_data/prepare_seed_data.py`), phải sinh lại file snapshot này:

```bash
python -m pip install -r requirements/extract.txt pandas pyarrow
python scripts/seed_source_db.py --dump-sql infra/source-db/init/seed_data_amazon.sql
```

`scripts/seed_source_db.py` (không có `--dump-sql`) vẫn dùng được để nạp
thẳng vào một `source-db` đang chạy sẵn (không cần restart container) —
idempotent, chạy lại không tạo dữ liệu trùng.

Để mô phỏng một lô dữ liệu nguồn mới (mặc định 20 product + 100 interaction,
tuỳ chỉnh bằng `--products`/`--interactions`):

```bash
python -m pip install -r requirements/extract.txt
python scripts/simulate_incremental_update.py
bash scripts/run_job_locally.sh extract
```

Mỗi source tạo một record trong `pipeline.extraction_runs`, kể cả khi không có
dữ liệu mới hoặc job thất bại. Để kiểm thử lỗi sau khi Parquet đã ghi nhưng
trước khi watermark commit, dùng PowerShell:

```powershell
$env:EXTRACT_FAIL_AFTER_WRITE = "interactions"
docker compose --profile jobs run --rm extract-job
Remove-Item Env:EXTRACT_FAIL_AFTER_WRITE
docker compose --profile jobs run --rm extract-job
```

Lần đầu phải fail và giữ nguyên watermark của `interactions`; lần retry phải
thành công, rồi lần kế tiếp phải extract zero rows.

Xem audit gồm cả định danh load của `dlt`:

```powershell
docker compose exec source-db psql -U reco_app -d recommendation -P pager=off -c "SELECT source_name, status, extracted_rows, jsonb_array_length(batches) AS n_batches, batches, error_message FROM pipeline.extraction_runs ORDER BY started_at DESC LIMIT 10;"
```

## Chạy Phase 2 lakehouse (Bronze → Silver → Gold)

Sau khi extract đã tạo dữ liệu (mục trên):

```bash
docker compose --profile jobs build transform-job
docker compose --profile jobs run --rm transform-job jobs.transform.build_bronze
docker compose --profile jobs run --rm transform-job jobs.transform.build_silver
docker compose --profile jobs run --rm transform-job jobs.transform.build_gold
```

(hoặc `make bronze` / `make silver` / `make gold` nếu có GNU Make). Bronze
append-only, idempotent theo `_extraction_run_id` (rerun không tạo dữ liệu
trùng); Silver/Gold tính lại toàn bộ mỗi lần chạy. Chi tiết:
[`docs/modules/phase-2-lakehouse.md`](docs/modules/phase-2-lakehouse.md).

Kiểm tra dữ liệu qua DuckDB (không cần cài gì thêm, chạy trong container):

```bash
docker compose --profile jobs run --rm --entrypoint python transform-job scripts/duckdb_query_iceberg.py bronze.products
```

Test tích hợp (PyIceberg + REST Catalog thật, không mock):

```bash
make test-integration
# hoặc: docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest /app/tests/integration -v
```

## Chạy Phase 3 feature store (Feast + Redis)

Sau khi Gold đã có dữ liệu (mục trên), sinh chuỗi hành vi theo user rồi đẩy
lên Redis qua Feast:

```bash
docker compose --profile jobs run --rm transform-job jobs.features.build_user_features
docker compose --profile jobs build materialize-job
docker compose --profile jobs run --rm materialize-job jobs.materialize.export_to_parquet
docker compose --profile jobs run --rm materialize-job jobs.materialize.feast_materialize
```

`gold.user_features` lưu chuỗi tối đa 10 item gần nhất/user (input cho
GRU4Rec ở Phase 4) — không phải đặc trưng dạng bảng phẳng. Chi tiết:
[`docs/modules/phase-3-feature-store.md`](docs/modules/phase-3-feature-store.md).

Kiểm tra nhanh một feature vector qua Feast SDK:

```bash
docker compose --profile jobs run --rm --entrypoint python materialize-job -c "
from feast import FeatureStore
store = FeatureStore(repo_path='/app/feature_repo')
print(store.get_online_features(
    features=['user_recent_items:item_sequence'],
    entity_rows=[{'user_id': 'u001'}],
).to_dict())
"
```

## Chạy Phase 4 training (GRU4Rec)

Sau khi Phase 3 đã có `gold.user_features`/`gold.item_features`, train model
ranking chính (GRU4Rec, ADR 0004) và đẩy version lên MLflow registry:

```bash
docker compose --profile jobs build training-job
docker compose --profile jobs run --rm training-job jobs.training.build_sequence_dataset  # sanity check
make train                                    # hoặc: docker compose --profile jobs run --rm training-job jobs.training.train_sequence
make promote                                  # gate: chỉ promote nếu vượt production hiện tại
make evaluate ARGS="--run-id <id>"            # re-evaluate độc lập 1 run đã train
```

MLflow UI: http://localhost:5000 (experiment `reco-mlops-gru4rec`). Chi
tiết kiến trúc, cách tách train/val, và lý do dùng alias MLflow thay stage:
[`docs/modules/phase-4-training.md`](docs/modules/phase-4-training.md).

## Chạy Phase 5 serving (FastAPI) + UI (Streamlit)

Sau khi có model ở alias `production` (Phase 4) và materialize sản phẩm
tương tự vào Redis:

```bash
docker compose --profile jobs run --rm training-job jobs.candidates.similar_items
make similar-items    # tương đương lệnh trên
```

`serving`/`ui` **không** thuộc profile `jobs` — tự chạy cùng `make up`
(giống `redis`/`mlflow`). Nếu chưa chạy hoặc cần build lại sau khi sửa code:

```bash
docker compose up -d --build serving ui
```

Kiểm tra nhanh:

```bash
curl "http://localhost:8000/recommend/homepage?user_id=<user_id thật>"
curl "http://localhost:8000/recommend/similar?item_id=<product_id thật>"
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{"user_id": "...", "item_id": "...", "event_type": "purchase"}'
curl http://localhost:8000/metrics
```

UI: http://localhost:8501 — nhập `user_id`, bấm "Đã mua" để xem gợi ý tự
cập nhật. Chi tiết luồng xử lý, quyết định dùng chuỗi Feast+Redis làm input
GRU (Hướng B), và lý do hạ version `feast`:
[`docs/modules/phase-5-serving.md`](docs/modules/phase-5-serving.md).

## Chạy Phase 6 orchestration (Airflow)

Bật riêng, không thuộc `make up` (mọi job đã chạy độc lập ở các phase
trước — Airflow chỉ gọi lại theo lịch/qua UI):

```bash
make airflow-up
```

Airflow UI: http://localhost:8080 (đăng nhập `AIRFLOW_ADMIN_USER`/
`AIRFLOW_ADMIN_PASSWORD` trong `.env`, mặc định `admin`/`admin`). 5 DAG:
`dag_ingest`, `dag_transform`, `dag_features_candidates`, `dag_materialize`,
`dag_training` — mỗi DAG `schedule=None`, trigger thủ công từ UI theo đúng
thứ tự `dag_ingest → dag_transform → dag_features_candidates →
{dag_materialize, dag_training}`. Mỗi task chạy trong 1 container job riêng
(`JobDockerOperator`, Docker-outside-of-Docker) — dừng ở task nào biết ngay
job nào lỗi. Chi tiết kiến trúc:
[`docs/modules/phase-6-orchestration.md`](docs/modules/phase-6-orchestration.md).

```bash
make airflow-down   # dừng riêng Airflow, không đụng các service khác
```

## Chạy Phase 7 monitoring (Prometheus/Grafana + Evidently)

Prometheus + Grafana + 2 exporter (`redis-exporter`, `statsd-exporter`) nằm
trong `docker-compose.override.yml`, **tự chạy cùng `make up`** (Compose tự
nạp file này, không cần cờ `-f` riêng). Grafana:
http://localhost:3000 (`admin` / `GRAFANA_ADMIN_PASSWORD` trong `.env`),
dashboard "Serving overview" đã có sẵn. Prometheus: http://localhost:9090.

Sinh traffic demo cho dashboard đỡ trống:

```bash
pip install locust
locust -f tests/load/locustfile.py --host http://localhost:8000
```

Giám sát dữ liệu/mô hình (Evidently) cần seed baseline 1 lần trước
(`amazone_data/data_split/train.parquet` → MinIO):

```bash
python -m pip install s3fs
python scripts/upload_monitoring_baseline.py
```

Rồi chạy qua Airflow (`dag_monitoring`, cần `make airflow-up` trước) hoặc
tay:

```bash
docker compose --profile jobs run --rm monitoring-job jobs.monitoring.data_drift_report
docker compose --profile jobs run --rm monitoring-job jobs.monitoring.model_quality_report
```

Report (HTML + JSON) ghi lên MinIO bucket `monitoring-reports`. Chi tiết:
[`docs/modules/phase-7-monitoring.md`](docs/modules/phase-7-monitoring.md).

## Chạy Phase 8 CI/CD (Jenkins)

Standalone, không thuộc `make up`:

```bash
docker compose -f infra/jenkins/docker-compose.jenkins.yml up -d --build
```

Jenkins UI: http://localhost:8090 (đăng nhập `JENKINS_ADMIN_USER`/
`JENKINS_ADMIN_PASSWORD` trong `.env`, mặc định `admin`/`admin`). Tạo 1 job
**Multibranch Pipeline** trỏ vào repo Git của bạn — `Jenkinsfile` ở repo
root tự chạy 6 stage (Lint, Unit Test, Integration Test, Model Smoke Test,
Build & Push Images). Muốn push image thật lên Docker Hub, set
`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` (Access Token, không phải mật khẩu
tài khoản) trong `.env` rồi restart container Jenkins để nạp lại credential
qua Configuration as Code.

Chạy từng stage tay (không cần Jenkins) — dùng đúng lệnh `Jenkinsfile` gọi:

```bash
docker compose build transform-job training-job materialize-job
docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest tests/unit/test_package_metadata.py -v
docker compose --profile jobs run --rm --entrypoint python extract-job -m pytest tests/unit/extract -v
docker compose --profile jobs run --rm --entrypoint python training-job -m pytest tests/unit/training -v
docker compose --profile jobs run --rm -e MLFLOW_TRACKING_URI=file:///tmp/mlruns --entrypoint python training-job -m pytest tests/model -v
```

Chi tiết đầy đủ (Integration Test cô lập, Build & Push, rollback model):
[`docs/modules/phase-8-cicd.md`](docs/modules/phase-8-cicd.md),
[`docs/runbook.md`](docs/runbook.md).

## Dừng hệ thống

```bash
make down
```

`make clean` sẽ xóa cả Docker volumes và dữ liệu local, vì vậy chỉ dùng khi
chủ động muốn cold start lại từ đầu.
