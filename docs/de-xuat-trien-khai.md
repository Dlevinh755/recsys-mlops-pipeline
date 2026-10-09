# ĐỀ XUẤT TRIỂN KHAI
## Pipeline MLOps — Hệ thống Gợi ý Sản phẩm (MVP)

Tài liệu này chia việc triển khai thành các **giai đoạn (phase) tuần tự**,
mỗi phase có: mục tiêu, đầu vào/phụ thuộc, checklist công việc cụ thể (map
trực tiếp vào các file trong `cau-truc-project.md`), và **tiêu chí hoàn
thành (Definition of Done)** để biết khi nào được coi là xong và chuyển
sang phase kế tiếp. Thứ tự phase được sắp theo nguyên tắc: **hạ tầng nền
trước, business logic sau, orchestration/observability cuối** — tránh vừa
build job vừa build hạ tầng chạy job cùng lúc.

---

## Phase 0 — Khung project & hạ tầng nền tảng

**Mục tiêu**: Có một repo chạy được `docker compose up` với các service hạ
tầng cốt lõi (MinIO, Iceberg REST Catalog, Postgres cho source-db và cho
MLflow), chưa có business logic.

**Việc cần làm**:
- [ ] Khởi tạo repo, `.gitignore`, `.dockerignore`, `.pre-commit-config.yaml`
      (black/ruff/isort), `Makefile` với các target cơ bản (`make up`,
      `make down`, `make test`)
- [ ] `docker-compose.yml` gốc: MinIO + REST Catalog + Postgres (source-db)
- [ ] `infra/minio/init-buckets.sh` — tạo bucket cho Iceberg và MLflow
      artifact
- [ ] `infra/iceberg-catalog/catalog.env`
- [ ] `infra/source-db/docker-compose.source-db.yml` +
      `init/schema.sql` (bảng có `created_at`/`updated_at`) +
      `init/seed_data.sql`
- [ ] `.env.example` liệt kê đủ biến môi trường sẽ dùng xuyên suốt dự án
- [ ] `libs/pyproject.toml` khởi tạo package `reco_mlops_libs` (rỗng, version
      `0.0.1`) — dựng khung sớm để các phase sau chỉ cần thêm module

**Definition of Done**: `docker compose up` chạy được, MinIO UI truy cập
được, kết nối được vào Postgres source-db bằng client bất kỳ và thấy bảng
seed sẵn. Chưa cần Iceberg table nào tồn tại ở bước này.

---

## Phase 1 — Extract & cơ chế watermark

**Mục tiêu**: Chứng minh được cơ chế incremental hoạt động đúng trên dữ liệu
thật, trước khi động đến Iceberg.

**Việc cần làm**:
- [ ] `scripts/seed_source_db.py` — nạp một phần dataset Amazon review vào
      source-db
- [ ] `jobs/extract/watermark.py` — đọc/ghi `last_watermark` bền vững (ví dụ
      lưu trong một bảng Postgres riêng, không lưu trong container)
- [ ] `jobs/extract/sources/interactions.py`, `products.py` — dùng **dlt**,
      query `WHERE updated_at > :last_watermark`
- [ ] `jobs/extract/run.py` — entrypoint gọi các source ở trên
- [ ] `scripts/simulate_incremental_update.py`
- [ ] `infra/docker/extract/Dockerfile`, `requirements/extract.txt`
- [ ] Test thủ công: chạy `run.py` hai lần liên tiếp, xen giữa là
      `simulate_incremental_update.py` — xác nhận lần chạy thứ hai chỉ lấy
      đúng phần mới

**Definition of Done**: Chạy `scripts/run_job_locally.sh extract` (đã có
khung tối thiểu từ Phase 0) hai lần với dữ liệu nguồn thay đổi ở giữa, kết
quả extract ra hai batch không trùng lặp, không bỏ sót. Output tạm thời có
thể ghi ra file Parquet cục bộ — **chưa cần Iceberg**, vì mục tiêu phase này
là validate watermark độc lập với lakehouse.

**Phụ thuộc**: Phase 0.

---

## Phase 2 — Lakehouse Bronze → Silver → Gold (Iceberg)

**Mục tiêu**: Đây là phase rủi ro kỹ thuật cao nhất của toàn dự án (PyIceberg
commit + REST Catalog) — nên làm sớm và test kỹ trước khi các phase sau phụ
thuộc vào nó.

**Việc cần làm**:
- [ ] `libs/src/reco_mlops_libs/iceberg/catalog.py` — kết nối REST Catalog
      dùng chung
- [ ] `libs/src/reco_mlops_libs/iceberg/schema_contracts/` —
      `bronze_interactions.py`, `silver_interactions.py`,
      `gold_item_features.py` (khai báo trước, kể cả khi transform chưa
      xong)
- [ ] `libs/src/reco_mlops_libs/iceberg/validators.py` — pandera, enforce
      schema contract trước khi commit
- [ ] `jobs/transform/iceberg_writer.py` — wrapper commit PyIceberg dùng
      chung cho bronze/silver/gold
- [ ] `jobs/transform/build_bronze.py` — nối output Phase 1 vào Bronze
      (append-only)
- [ ] `sql/silver/`, `jobs/transform/build_silver.py` — làm sạch, chuẩn hóa
      kiểu, khử trùng lặp
- [ ] `sql/gold/`, `jobs/transform/build_gold.py` — aggregation theo
      user/item
- [ ] `jobs/maintenance/compact_iceberg.py` (có thể làm cuối phase này hoặc
      dời sang Phase 7 cùng lúc với `dag_maintenance.py`)
- [ ] `tests/integration/` — test PyIceberg commit + REST Catalog với MinIO
      + DuckDB thật qua docker-compose test (**không mock** — theo đúng lưu
      ý trong `cau-truc-project.md`, đây là phần rủi ro lớn nhất nên phải
      test bằng hạ tầng thật)
- [ ] `infra/docker/transform/Dockerfile`, `requirements/transform.txt`

**Definition of Done**: Có 3 bảng Iceberg (bronze/silver/gold) truy vấn
được qua DuckDB, dữ liệu chạy xuyên suốt từ extract → gold không lỗi
schema. `pytest tests/integration/` pass. Chạy lại `build_bronze.py` với dữ
liệu incremental mới từ Phase 1 không tạo dữ liệu trùng.

**Phụ thuộc**: Phase 0, Phase 1.

---

## Phase 3 — Feature Store (Feast + Redis)

**Mục tiêu**: Có online feature lookup độ trễ thấp, sẵn sàng cho cả training
và serving.

**Việc cần làm**:
- [ ] `jobs/features/build_user_features.py`,
      `build_item_features.py`, `build_interaction_features.py` (ghi vào
      Gold — có thể đã làm một phần ở Phase 2, hoàn thiện ở đây)
- [ ] `jobs/materialize/export_to_parquet.py` — Iceberg → Parquet snapshot
      (bước bắt buộc vì Feast chưa đọc Iceberg ổn định)
- [ ] `feature_repo/` — `feature_store.yaml`, `entities.py`,
      `features_user.py`, `features_item.py`, `data_sources.py`
      (FileSource trỏ Parquet snapshot)
- [ ] `jobs/materialize/feast_materialize.py` — đẩy offline → Redis
- [ ] Redis service thêm vào `docker-compose.yml`

**Definition of Done**: `feast apply` chạy được trong `feature_repo/`,
`feast materialize` đẩy dữ liệu vào Redis thành công, và có thể query một
feature vector bằng Feast SDK trả về đúng giá trị đã tính ở Gold.

**Phụ thuộc**: Phase 2.

---

## Phase 4 — Training & Model Registry

**Mục tiêu**: Có model ranking huấn luyện được, tracking đầy đủ trên
MLflow, có gate promotion.

**Việc cần làm**:
- [ ] `infra/mlflow/docker-compose.mlflow.yml` + `mlflow.env` (backend store
      Postgres riêng — **không dùng chung** với source-db)
- [ ] `jobs/training/build_training_set.py` — temporal split, group theo
      user/query
- [ ] `jobs/training/negative_sampling.py`
- [ ] `jobs/training/train_ranker.py` — LightGBMRanker, log param/metric,
      **và log `iceberg_snapshot_id`** qua
      `libs/src/reco_mlops_libs/mlflow_utils/logging_helpers.py` (lineage —
      làm ngay từ đầu, không phải thêm sau)
- [ ] `jobs/training/evaluate.py` — NDCG, Recall, MAP, Coverage
- [ ] `libs/src/reco_mlops_libs/mlflow_utils/registry_client.py` —
      `get_production_version()`, dùng chung cho `promote.py` và
      `model_loader.py` ở Phase 5
- [ ] `jobs/training/promote.py` — chỉ promote khi metric vượt ngưỡng
- [ ] `infra/docker/training/Dockerfile`, `requirements/training.txt`
- [ ] `jobs/candidates/similar_items.py`, `popular_items.py` (candidate
      generation cho serving — có thể làm cuối phase này)
- [ ] `libs/src/reco_mlops_libs/ranking/base.py` — định nghĩa interface mỏng
      `predict(user_id, candidate_items) -> scores`, **không lộ chi tiết
      LightGBM (DataFrame phẳng) ra ngoài interface**; `train_ranker.py` lưu
      model implement interface này. Việc này làm ngay ở phase hiện tại vì
      rẻ (vài chục dòng), tránh phải sửa lại `homepage.py`/`similar.py`/
      `model_loader.py` một lần nữa nếu sau này thêm model khác kiểu input
      (xem Phase 9 — không bắt buộc làm Phase 9 ngay, nhưng interface này
      nên có sẵn để không tự khoá đường)

**Definition of Done**: Chạy toàn bộ chuỗi build_training_set → train →
evaluate → promote ngoài Airflow (`scripts/run_job_locally.sh`), thấy
experiment + model artifact + `iceberg_snapshot_id` xuất hiện trên MLflow
UI, và registry có version ở trạng thái "production". `train_ranker.py` trả
về một object implement đúng `predict(user_id, candidate_items) -> scores`,
không phải gọi trực tiếp `model.predict(dataframe)` ở tầng ngoài.

**Phụ thuộc**: Phase 3 (offline feature từ Gold/Parquet snapshot).

---

## Phase 5 — Serving (FastAPI)

**Mục tiêu**: API trả gợi ý thật, có cache, có fallback, không bao giờ trả
5xx vì thiếu model/feature.

**Việc cần làm**:
- [ ] `serving/app/core/config.py`
- [ ] `serving/app/core/model_loader.py` — poll registry định kỳ qua
      `registry_client.py` (dùng chung với `promote.py`), cache model trong
      bộ nhớ — **đây là điểm khép vòng train → promote → serve, làm đúng
      ngay từ đầu thay vì load-once**; gọi model **qua interface
      `libs/src/reco_mlops_libs/ranking/base.py`** đã định nghĩa ở Phase 4,
      không gọi trực tiếp API cụ thể của LightGBM
- [ ] `serving/app/core/cache.py` — Redis cache check
- [ ] `serving/app/core/fallback.py` — fallback popular items khi model/
      feature không sẵn sàng
- [ ] `serving/app/api/homepage.py`, `similar.py`
- [ ] `serving/app/core/metrics.py` — `prometheus-fastapi-instrumentator`
      (có thể làm cùng lúc, vì gần như miễn phí về công sức)
- [ ] `infra/docker/serving/Dockerfile`, `requirements/serving.txt`
- [ ] `serving/tests/` — test riêng cho từng nhánh: cache hit, cache miss +
      model available, fallback path
- [ ] `jobs/materialize/export_similar_items_to_redis.py` — đẩy kết quả
      `similar_items.py` (Phase 4) sang Redis, key riêng `similar_items:
      {item_id}`, tách namespace khỏi online feature của Feast
- [ ] `serving/app/core/source_db_writer.py` — ghi 1 dòng vào bảng
      `interactions` của `source-db`, `updated_at = now()` (tái dùng schema
      đã có từ Phase 0, đi qua đúng watermark ở Phase 1)
- [ ] `serving/app/core/recency_boost.py` — đọc `recent_items:{user_id}` từ
      Redis, tra `similar_items:{item_id}` tương ứng, đẩy lên đầu danh sách
      candidate trước khi trả response
- [ ] `serving/app/api/interact.py` — `POST /interact` nhận
      `{user_id, item_id, event_type}`, gọi cả `source_db_writer.py` (ghi
      source-db) lẫn ghi trực tiếp Redis `recent_items:{user_id}` (list, có
      TTL vài chục phút)
- [ ] `ui/app.py` (Streamlit, ngoài `serving/`) — danh sách gợi ý + nút "Đã
      mua" gọi `/interact` rồi gọi lại `/recommend/homepage`
- [ ] `infra/docker/ui/Dockerfile`, `requirements/ui.txt`

**Definition of Done**: `curl` vào `/recommend/homepage` và
`/recommend/similar` trả kết quả hợp lệ trong 3 tình huống: cache hit, cache
miss (gọi model thật), và model/feature không sẵn sàng (trả fallback, không
lỗi 5xx). `/metrics` trả về đúng định dạng Prometheus. Gọi `POST /interact`
với một cặp user/item đã tồn tại, sau đó gọi lại `/recommend/homepage` cho
đúng user đó — danh sách trả về phải phản ánh rõ sự thay đổi (item liên quan
lên đầu). Chạy `ui/app.py`, bấm "Đã mua" trên giao diện, thấy danh sách gợi ý
tự cập nhật mà không cần thao tác gì thêm ngoài bấm nút.

**Phụ thuộc**: Phase 3 (Redis online store), Phase 4 (model + registry
client, `similar_items.py`).

---

## Phase 6 — Orchestration (Airflow)

**Mục tiêu**: Toàn bộ job ở Phase 1–5 chạy được theo lịch, tự động, qua
DockerOperator — không còn phải chạy tay bằng `run_job_locally.sh`.

Phase này cố ý đặt **sau** khi từng job đã chạy độc lập và test được ngoài
Airflow, đúng nguyên tắc "Airflow chỉ orchestration" — tránh vừa debug logic
job vừa debug DAG cùng lúc.

**Việc cần làm**:
- [ ] `airflow/plugins/operators/job_docker_operator.py` — wrapper
      DockerOperator dùng chung mọi DAG
- [ ] `airflow/dags/dag_ingest.py` → gọi `python -m jobs.extract.run`
- [ ] `airflow/dags/dag_transform.py` → build_silver, build_gold
- [ ] `airflow/dags/dag_features_candidates.py`
- [ ] `airflow/dags/dag_training.py`
- [ ] `airflow/dags/dag_materialize.py`
- [ ] `airflow/dags/dag_maintenance.py` → `compact_iceberg.py`
- [ ] `infra/docker/airflow/Dockerfile`
- [ ] Mỗi image gắn tag theo `git rev-parse --short HEAD`, DAG tham chiếu
      đúng tag đó (đúng nguyên tắc versioning trong báo cáo)

**Definition of Done**: Từ Airflow UI, trigger được `dag_ingest` →
`dag_transform` → `dag_training` → `dag_materialize` theo đúng thứ tự phụ
thuộc, mỗi task chạy trong container riêng, dừng ở task nào là biết ngay
job nào lỗi (nhờ mỗi job đã test độc lập ở phase trước).

**Phụ thuộc**: Phase 1–5 đã chạy được độc lập.

---

## Phase 7 — Monitoring (Prometheus/Grafana + Evidently)

**Mục tiêu**: Tách rõ hai lớp giám sát — vận hành và chất lượng dữ liệu/mô
hình — theo đúng thiết kế đã chốt.

**Việc cần làm**:
- [ ] `infra/monitoring/prometheus/prometheus.yml` — scrape serving,
      airflow, redis. **Lưu ý**: `redis` và `airflow` không tự expose
      `/metrics` chuẩn Prometheus — cần thêm 2 sidecar mới nạp được:
      `redis_exporter` (`oliver006/redis_exporter`, trỏ
      `redis://redis:6379`, expose `:9121/metrics`) và `statsd_exporter`
      (`prom/statsd-exporter`, expose `:9102/metrics`, nhận UDP `:9125` từ
      Airflow) — Airflow (service Phase 6) cần bật thêm
      `AIRFLOW__METRICS__STATSD_ON=True` +
      `AIRFLOW__METRICS__STATSD_HOST=statsd-exporter` +
      `AIRFLOW__METRICS__STATSD_PORT=9125` trong `docker-compose.yml` chính
      (biến Airflow, không đặt trong override vì không phải service chỉ
      phục vụ observability). `prometheus.yml` scrape 4 target: `serving`,
      `redis-exporter`, `statsd-exporter` (đại diện cho airflow) — không
      phải 3 target như tên gọi "scrape serving/airflow/redis" gợi ý.
- [ ] `infra/monitoring/grafana/provisioning/` +
      `dashboards/serving-overview.json` (latency p95, cache hit, error
      rate)
- [ ] `docker-compose.override.yml` khai báo Prometheus + Grafana +
      `redis-exporter` + `statsd-exporter` (tách khỏi compose chính, để có
      thể bật/tắt khi cần). Thêm `GRAFANA_ADMIN_PASSWORD` vào `.env.example`
      (theo đúng pattern các password khác đã có — `MINIO_ROOT_PASSWORD`,
      `SOURCE_DB_PASSWORD`...).
- [ ] `jobs/monitoring/data_drift_report.py` — Evidently, so Silver hôm nay
      vs baseline
- [ ] `jobs/monitoring/model_quality_report.py` — prediction drift, feature
      drift
- [ ] `jobs/monitoring/report_sink.py` — ghi report lên MinIO
- [ ] `airflow/dags/dag_monitoring.py`
- [ ] `infra/docker/monitoring/Dockerfile`, `requirements/monitoring.txt`
- [ ] `tests/load/locustfile.py` — gọi `/recommend/homepage` và
      `/recommend/similar`, dùng để tạo traffic demo cho Grafana (chạy thủ
      công, không đưa vào CI)

**Definition of Done**: `docker compose -f docker-compose.yml -f
docker-compose.override.yml up` → Grafana có dashboard sẵn không cần setup
tay. Chạy `locust -f tests/load/locustfile.py` vài phút → thấy latency/
cache-hit thật xuất hiện trên dashboard thay vì trống. `dag_monitoring`
chạy ra report HTML/JSON trên MinIO, đọc được và thấy số liệu drift hợp lý.

**Phụ thuộc**: Phase 5 (serving có `/metrics`), Phase 2 (Silver để so
baseline).

---

## Phase 8 — CI/CD, test, tài liệu, đóng gói `libs/`

**Mục tiêu**: Siết lại chất lượng và khả năng tái lập của toàn bộ project
— phase này có thể làm song song (không chặn) với Phase 6–7 ở phần
CI/lint, nhưng nên hoàn thiện trước khi bảo vệ khóa luận.

**Việc cần làm**:
- [ ] Đóng gói `libs/` thành package cài được thật sự (`pip install -e
      ../libs`), thêm `CHANGELOG.md`, cập nhật mọi
      `requirements/<job>.txt` khai `-e ../libs` — tránh tình trạng image
      chạy code `libs/` cũ
- [ ] `.github/workflows/ci.yml` — lint, unit test, integration test
- [ ] `tests/model/sample_data/` (vài trăm dòng, seed cố định) +
      `tests/model/test_training_smoke.py` — job `model-smoke-test` trong
      `ci.yml`
- [ ] `tests/data_quality/test_train_serve_skew.py` — so feature offline
      (Gold) vs online (Redis)
- [ ] `serving/tests/test_model_reload.py` — xác nhận serving nhận model
      version mới sau khi `promote.py` chạy (test cho cơ chế polling ở
      Phase 5)
- [ ] `.github/workflows/build-push-images.yml`
- [ ] `.github/workflows/model-cd.yml` (ping/thông báo, không bắt buộc vì
      polling ở serving đã tự đủ)
- [ ] `docs/architecture.md`, `docs/runbook.md` (bao gồm quy trình rollback
      model thủ công), `docs/data_dictionary.md`, `docs/model_card.md`
- [ ] (Tùy chọn, nên có nếu còn thời gian) `docs/decisions/` kiểu ADR —
      ghi lại các quyết định kiến trúc chính (Spark→DuckDB, không dùng dbt…)
      để giải trình lúc bảo vệ

**Definition of Done**: PR bất kỳ chạy `ci.yml` tự động, bao gồm
model-smoke-test; toàn bộ tài liệu trong `docs/` phản ánh đúng những gì đã
code (không viết trước khi có code, tránh lệch giữa tài liệu và thực tế).

**Phụ thuộc**: Toàn bộ Phase 1–7 (vì test/docs cần có thứ để test/mô tả).

---

## Phase 9 — Sequence Model (mở rộng, KHÔNG thuộc MVP1 chính)

**Tách riêng có chủ đích**: phase này chỉ nên bắt đầu **sau khi MVP1 (Phase
0–8) đã chạy hoàn chỉnh và demo được**. Không chặn, không nằm trên đường
găng của MVP1 — coi như nhánh rẽ, có thể bỏ qua hoàn toàn nếu hết thời gian
mà không ảnh hưởng tính đầy đủ của khóa luận.

**Mục tiêu**: Có thêm một model ranking dựa trên chuỗi hành vi
(mini-GRU4Rec), huấn luyện nhanh trên CPU, so sánh được công bằng với
LightGBM (Phase 4) trên cùng bộ metric.

**Điều kiện tiên quyết** (đã có sẵn nếu Phase 4–5 làm đúng): interface
`libs/src/reco_mlops_libs/ranking/base.py` đã tồn tại — đây là lý do phase
này không cần sửa lại `homepage.py`/`similar.py`, chỉ cần thêm implementation
mới của cùng interface.

**Việc cần làm**:
- [ ] `jobs/training/build_sequence_dataset.py` — đọc từ Gold layer (Iceberg,
      không đổi source of truth), sinh chuỗi item_id gần nhất theo user, pad/
      truncate độ dài cố định (ví dụ max_len=20)
- [ ] `jobs/training/train_sequence.py` — PyTorch thuần: Embedding (dim~32) →
      GRU 1 lớp (hidden~64) → Linear + **sampled softmax** (không tính
      softmax đầy đủ trên toàn vocab — đây là phần tốn CPU nhất nếu bỏ qua
      negative sampling); log vào MLflow cùng registry với `train_ranker.py`,
      thêm tag `model_type=sequence` để phân biệt, vẫn log
      `iceberg_snapshot_id` như Phase 4 (dùng chung
      `logging_helpers.py`)
- [ ] Model output implement đúng interface `predict(user_id, candidate_items)
      -> scores` đã định nghĩa ở Phase 4 — phần input là DataFrame hay chuỗi
      tensor chỉ là chi tiết bên trong implementation, không lộ ra ngoài
- [ ] `jobs/training/evaluate.py` — tái sử dụng nguyên bộ NDCG/Recall/MAP đã
      có, chạy trên cùng tập test với LightGBM để so sánh công bằng
- [ ] `requirements/training.txt` — thêm `torch` (CPU build), không thêm
      Transformers4Rec/PyTorch-Lightning để giữ nhẹ
- [ ] (Tuỳ chọn) `docs/model_card.md` — bổ sung mục so sánh 2 model, kể cả
      nếu kết quả là "sequence model chưa vượt LightGBM ở quy mô dữ liệu
      MVP" — đây vẫn là kết luận hợp lệ nếu quy trình so sánh làm đúng

**Definition of Done**: `train_sequence.py` chạy hết trên CPU trong thời
gian hợp lý (tham khảo: vài phút đến ~30 phút tuỳ khối lượng interaction,
không cần GPU), model xuất hiện trên MLflow với tag `model_type=sequence`,
`evaluate.py` cho ra bảng so sánh NDCG/Recall/MAP giữa 2 model trên cùng tập
test. `model_loader.py` (Phase 5) load được model này mà **không cần sửa**
`homepage.py`/`similar.py` — chỉ đổi version đang "production" trong
registry.

**Phụ thuộc**: MVP1 hoàn chỉnh (Phase 0–8), đặc biệt là interface ở Phase 4
và `model_loader.py` ở Phase 5.

---

## Tổng quan phụ thuộc giữa các phase

```
Phase 0 (hạ tầng nền)
   │
   ▼
Phase 1 (extract + watermark)
   │
   ▼
Phase 2 (Bronze/Silver/Gold — Iceberg)  ◀── rủi ro kỹ thuật cao nhất, làm sớm
   │
   ▼
Phase 3 (Feature Store) ──────────────┐
   │                                  │
   ▼                                  ▼
Phase 4 (Training/Registry)     Phase 5 (Serving)
   │                                  │
   └──────────────┬───────────────────┘
                   ▼
          Phase 6 (Airflow orchestration)
                   │
                   ▼
          Phase 7 (Monitoring)
                   │
                   ▼
          Phase 8 (CI/CD, test, docs)
                   │
                   ▼  ── MVP1 hoàn chỉnh tại đây ──
                   │
          Phase 9 (Sequence Model — mở rộng, tuỳ chọn)
```

## Gợi ý cách dùng file này khi triển khai

- Coi mỗi checklist item là một commit hoặc một PR nhỏ — không gộp cả phase
  vào một commit, vì Phase 2 (Iceberg) đặc biệt dễ phát sinh lỗi cần
  bisect lại.
- Không nên bắt đầu Phase 6 (Airflow) trước khi từng job ở Phase 1–5 đã
  chạy tay được qua `scripts/run_job_locally.sh` — nếu vội gộp sớm, khi DAG
  lỗi sẽ không biết là lỗi logic job hay lỗi cấu hình Airflow.
- Ngoài phạm vi chính (đã nêu rõ trong báo cáo kỹ thuật): CDC/streaming,
  Kubernetes production-scale, A/B testing — không đưa vào các phase trên,
  chỉ giữ placeholder (`infra/k8s/helm/`) như đã thiết kế.
- `POST /interact`, `ui/` và `tests/load/locustfile.py` (thêm ở Phase 5 và
  Phase 7) không phải business logic cốt lõi — đây là lớp phục vụ demo/trình
  bày. `/interact` vẫn đi qua đúng cơ chế watermark đã có (không phải
  streaming/CDC), nên không mâu thuẫn với phần "ngoài phạm vi chính" ở trên;
  chi tiết ranh giới đã giải thích trong `bao-cao-ky-thuat.md` mục 8.1.
- **Phase 9 là tuỳ chọn, không phải yêu cầu để coi MVP1 hoàn thành.** Chỉ
  nên bắt đầu sau khi Phase 0–8 đã chạy và demo ổn định. Điều duy nhất cần
  làm sớm (ở Phase 4–5, không phải chờ tới Phase 9) là định nghĩa interface
  `predict(user_id, candidate_items) -> scores` — rẻ để làm ngay, đắt để vá
  lại sau nếu bỏ qua.
