# Phase 7 — Monitoring (Prometheus/Grafana + Evidently)

> Trạng thái: **Hoàn thành**. Đối chiếu với `de-xuat-trien-khai.md` mục
> "Phase 7" và `bao-cao-ky-thuat.md` mục 9 (hai lớp giám sát tách biệt).
> Phụ thuộc: Phase 5 (`serving` có `/metrics`), Phase 2 (Silver để so
> baseline), Phase 6 (`JobDockerOperator` tái dùng nguyên vẹn cho
> `dag_monitoring`/`dag_maintenance`).

Thư mục code: `jobs/monitoring/`,
`jobs/maintenance/`,
`infra/monitoring/`.

---

## 1. Mục tiêu ban đầu

Tách rõ hai lớp giám sát: **vận hành** (Prometheus/Grafana — latency p95,
cache hit, error rate) và **dữ liệu/mô hình** (Evidently — data drift, model
quality). Không dựng dashboard/report thủ công khi demo.

## 2. Đã triển khai

### 2.1 Giám sát vận hành (`docker-compose.override.yml`)

4 service: `redis-exporter`, `statsd-exporter`, `prometheus`, `grafana` —
tất cả vào **override**, không phải compose chính (CLAUDE.md: "không thêm
dependency chỉ phục vụ observability vào compose chính"). `redis`/`airflow`
không tự expose `/metrics` chuẩn Prometheus nên cần 2 exporter làm cầu nối
(`redis_exporter` đọc `INFO` từ Redis; `statsd-exporter` nhận UDP từ
Airflow — bật qua `AIRFLOW__METRICS__STATSD_*` thêm vào `&airflow-env`
**trong `docker-compose.yml` chính**, vì đây là biến của service Airflow đã
tồn tại, không phải service observability mới).

`infra/monitoring/prometheus/prometheus.yml` scrape 3 target: `serving`,
`redis-exporter`, `statsd-exporter` — không phải "serving/airflow/redis"
theo nghĩa đen. Grafana tự provision datasource (Prometheus) + dashboard
(`serving-overview.json`: request rate, latency p95, error rate, **cache
hit ratio**) qua `infra/monitoring/grafana/provisioning/` — không cần thao
tác tay sau `docker compose up`.

**Cache hit ratio** cần thêm 1 `Counter` mới
(`cache_lookups_total{result=hit|miss}`, `serving/app/core/cache.py`) —
`bao-cao-ky-thuat.md` mục 9.1 đã nêu tên chỉ số này từ Phase 5 nhưng chưa
wiring, điền nốt ở đây.

`docker-compose.override.yml` được Compose **tự động nạp** cùng
`docker-compose.yml` (không cần cờ `-f` riêng) — comment sẵn trong file từ
Phase 0 đã nói rõ ý này, không phải hành vi bất ngờ.

### 2.2 Giám sát dữ liệu/mô hình (`jobs/monitoring/`, image `monitoring-job`)

- `scripts/upload_monitoring_baseline.py` — chạy tay 1 lần trên host, đẩy
  `amazone_data/data_split/train.parquet` lên bucket MinIO mới
  `MONITORING_BASELINE_BUCKET` (`monitoring-baseline`). Cần thiết vì
  `amazone_data/` nằm ngoài Docker build context của mọi job và
  `monitoring-job` (containerized, chạy qua Airflow) không có quyền đọc
  file host — xem mục 3.
- `jobs/monitoring/data_drift_report.py` — so `rating` +
  `is_cold_start_item` (dẫn xuất: `product_id` không có trong tập
  `parent_asin` của baseline) giữa baseline (MinIO) và `silver.interactions`
  hiện tại (PyIceberg). `Evidently Report(DataDriftPreset())`, ghi cả HTML
  và JSON.
- `jobs/monitoring/model_quality_report.py` — vocab coverage (% sản phẩm
  active mà model `production` biết tới, qua `item_vocab.json`) + xu hướng
  `val_ndcg_at_10` qua các version đã đăng ký MLflow.
- `jobs/monitoring/report_sink.py` — ghi report lên bucket
  `MONITORING_REPORTS_BUCKET` (`monitoring-reports`), cùng pattern
  `s3fs.S3FileSystem` của `jobs/materialize/export_to_parquet.py`.
- `infra/docker/monitoring/Dockerfile` + `requirements/monitoring.txt`
  (`evidently==0.4.40`, `s3fs` — đã dry-run pip trước khi chốt version,
  không xung đột với `mlflow==3.12.0`/`pyiceberg==0.12.0`/`pandas==2.3.3`
  đã pin trong `libs/`).
- `airflow/dags/dag_monitoring.py` — 2 task độc lập, dùng lại nguyên vẹn
  `JobDockerOperator` (Phase 6).

### 2.3 `tests/load/locustfile.py`

Tự lấy user/item thật qua `GET /users` + response `/recommend/homepage`
(không hard-code id). Chạy tay, không CI.

### 2.4 Carryover từ Phase 2/6: `jobs/maintenance/compact_iceberg.py`

Đã hứa "dời sang Phase 7" ở cả `phase-2-lakehouse.md` và
`phase-6-orchestration.md` — làm nốt lần này. Chỉ compact **Bronze**
(`bronze.products`/`bronze.interactions`, append-only, tích tiểu file theo
từng lần extract) — Silver/Gold đã `overwrite()` toàn bộ mỗi lần chạy nên
không cần. Kiểm tra trực tiếp trên `pyiceberg==0.12.0` (bản đã pin) trước
khi code: `table.maintenance` mới chỉ có `expire_snapshots`, chưa có action
`rewrite_data_files`/compaction native — nên compact bằng cách đọc toàn bộ
(`scan().to_arrow()`) rồi `overwrite()` lại chính nó, tái dùng
`jobs/transform/iceberg_writer.py::overwrite()` sẵn có. Vẫn là compaction
Iceberg đúng nghĩa (snapshot mới ít file hơn, snapshot cũ + file cũ vẫn
còn, time-travel được), không phải hack. `airflow/dags/dag_maintenance.py`
gọi qua image `transform-job` có sẵn (không dựng image mới).

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Lý do |
|---|---|---|
| `data_drift_report.py` so Silver với "baseline" (không nói rõ nguồn) | Baseline = `train.parquet` upload lên MinIO qua script host, không đọc trực tiếp từ `amazone_data/` trong container | `amazone_data/` ngoài Docker build context của mọi job, chỉ script host (`seed_source_db.py`) đụng tới được từ trước — đúng CLAUDE.md nguyên tắc #6 (không phụ thuộc filesystem host từ trong container) |
| `model_quality_report.py`: "prediction drift, feature drift" | Thu hẹp còn: vocab coverage + xu hướng `val_ndcg_at_10` qua version | `serving` không log lại prediction nào (chỉ cache Redis TTL ngắn) — không có dữ liệu prediction lịch sử thật để so drift. Thêm log mới từ `serving` là ngoại lệ kiến trúc thứ 2 ngoài `/interact`, cần ADR, ngoài phạm vi phase chỉ thêm monitoring. 2 chỉ số thay thế là thật, có sẵn dữ liệu, không giả |
| `dag_maintenance.py` (Phase 6, rồi lại dời) | Làm ở Phase 7 như đã hứa | Không lùi tiếp lần 2 |

Không cần ADR mới — cả 2 điểm trên là áp dụng đúng nguyên tắc sẵn có
(#6) hoặc thu hẹp phạm vi có ghi chú rõ (như Phase 5 đã làm), không đổi
nguyên tắc kiến trúc.

## 4. Definition of Done — đối chiếu

| Yêu cầu | Kết quả |
|---|---|
| Grafana có dashboard sẵn không cần setup tay | ✅ — `GET /api/search` trả về "Serving overview" ngay sau `docker compose up`, datasource Prometheus tự có |
| Chạy Locust vài phút → latency/cache-hit thật xuất hiện thay vì trống | ✅ — chạy 30s/5 user thật (251 request, 0% fail), Prometheus query trực tiếp: request rate ~3.18 req/s (`/recommend/homepage`), p95 latency ~9ms, cache hit ratio 84.7% |
| `dag_monitoring` chạy ra report HTML/JSON trên MinIO, đọc được, số liệu drift hợp lý | ✅ — `data_drift_report`: baseline 16222 dòng, current 16245 dòng, cold-start ratio 0.05% (hợp lý — dữ liệu hiện tại gần như trùng baseline, chưa nạp batch `incoming/`); `model_quality_report`: coverage 100% (1565/1565), metric trend 3 version giảm dần đúng thực tế |

## 5. Việc còn để lại cho phase sau

- Alertmanager (cảnh báo tự động khi vượt ngưỡng) — đã nêu ở
  `bao-cao-ky-thuat.md` mục "hạn chế còn tồn đọng".
- Đóng vòng lặp drift → retrain tự động (Evidently phát hiện nhưng không tự
  kích hoạt `dag_training`) — gợi ý dùng Airflow Variable, cũng đã nêu sẵn.
- `compact_iceberg.py` chưa gọi `expire_snapshots()` — file cũ (đã bị thay
  bởi lần compact) vẫn chiếm chỗ trên MinIO cho tới khi có chính sách
  retention rõ ràng (giữ bao nhiêu snapshot/bao lâu).
- Tag image vẫn `latest` (gap có sẵn từ Phase 0, cần git — xem
  `phase-6-orchestration.md`).

## 6. Danh sách file đã triển khai

### Giám sát vận hành

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `docker-compose.override.yml` | 4 service quan sát | Tự nạp cùng `docker-compose.yml`, không cần `-f` riêng |
| `infra/monitoring/prometheus/prometheus.yml` | Scrape config | 3 target: serving/redis-exporter/statsd-exporter |
| `infra/monitoring/grafana/provisioning/` | Auto-provision datasource + dashboard | `dashboards.yml` trỏ `/var/lib/grafana/dashboards` |
| `infra/monitoring/grafana/dashboards/serving-overview.json` | Dashboard chính | 4 panel: rate, p95, error rate, cache hit |
| `serving/app/core/cache.py` | +`cache_lookups_total` Counter | label `result=hit|miss` |
| `.env.example` | +`PROMETHEUS_PORT`, `GRAFANA_PORT`, `GRAFANA_ADMIN_PASSWORD` | |

### Giám sát dữ liệu/mô hình (image `monitoring-job`)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `infra/docker/monitoring/Dockerfile` | Image riêng | `evidently` chỉ ở đây, không lây sang job khác |
| `requirements/monitoring.txt` | Dependency | `evidently==0.4.40`, `s3fs` |
| `jobs/monitoring/report_sink.py` | Ghi report lên MinIO | Bucket `MONITORING_REPORTS_BUCKET` |
| `jobs/monitoring/data_drift_report.py` | Evidently data drift | Baseline từ `MONITORING_BASELINE_BUCKET` |
| `jobs/monitoring/model_quality_report.py` | Vocab coverage + metric trend | Đọc MLflow registry, `gold.item_features` |
| `scripts/upload_monitoring_baseline.py` | Seed baseline (chạy tay 1 lần) | Cùng kiểu `seed_source_db.py` |
| `airflow/dags/dag_monitoring.py` | 2 task độc lập | `JobDockerOperator` tái dùng từ Phase 6 |
| `infra/minio/init-buckets.sh`, `docker-compose.yml` (`minio-init`) | +2 bucket mới | `monitoring-baseline`, `monitoring-reports` |

### Carryover `compact_iceberg.py`

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/maintenance/compact_iceberg.py` | Compact Bronze | Overwrite-based (pyiceberg 0.12.0 chưa có action native) |
| `airflow/dags/dag_maintenance.py` | 1 task | Dùng image `transform-job` có sẵn |
| `infra/docker/transform/Dockerfile` | +`COPY jobs/maintenance/` | |

### Load test

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `tests/load/locustfile.py` | Sinh traffic demo | Tự lấy user/item thật qua API, không hard-code |
