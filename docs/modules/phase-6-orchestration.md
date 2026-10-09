# Phase 6 — Orchestration (Airflow)

> Trạng thái: **Hoàn thành**. Đối chiếu với `de-xuat-trien-khai.md` mục
> "Phase 6" và `bao-cao-ky-thuat.md` (Orchestration: Airflow + DockerOperator).
> Phụ thuộc: Phase 1–5 đã chạy độc lập và kiểm chứng qua
> `docker compose --profile jobs run --rm <job> [module]`.

Thư mục code: `airflow/`.

---

## 1. Mục tiêu ban đầu

Toàn bộ job Phase 1–5 chạy được theo lịch/qua UI Airflow bằng
`DockerOperator`, không còn phải chạy tay bằng `run_job_locally.sh`/`make`.
DAG chỉ gọi lại đúng module đã có, không chứa business logic mới
(CLAUDE.md nguyên tắc #1).

## 2. Đã triển khai

### 2.1 Hạ tầng Airflow (`docker-compose.yml`)

4 service mới, **không** thuộc `make up` mặc định (bật riêng bằng
`make airflow-up`, cùng lý do CLAUDE.md "không thêm dependency chỉ phục vụ
demo vào compose chính" — mọi job đã chạy và kiểm chứng độc lập, Airflow chỉ
là lớp gọi lại):

- `airflow-db` — Postgres metadata, cùng pattern `mlflow-db`/`catalog-db`.
- `airflow-init` — one-shot: `airflow db migrate` + tạo user admin local dev.
- `airflow-webserver`, `airflow-scheduler` — `LocalExecutor` (đủ cho MVP,
  không cần Celery/Redis broker riêng).

Cả 3 image sau build từ `infra/docker/airflow/Dockerfile`
(`apache/airflow:2.10.4-python3.12` + cài thêm Docker CLI +
`docker-compose-plugin` qua apt repo chính thức của Docker — ảnh gốc không
có sẵn — và `apache-airflow-providers-docker` qua `requirements/airflow.txt`,
provider chứa `DockerOperator`).

`airflow-webserver`/`-scheduler` mount:
- `./airflow/dags`, `./airflow/plugins`, `./airflow/logs`
- `.:/opt/airflow/project:ro` — để operator gọi `docker compose config`
- `/var/run/docker.sock:/var/run/docker.sock` — Docker-outside-of-Docker,
  chạy container job như "anh em" thay vì "con" của container Airflow.

### 2.2 `airflow/plugins/operators/job_docker_operator.py`

`JobDockerOperator(DockerOperator)` — mọi DAG dùng chung 1 class này, nhận
`compose_service` (image nào) + `module` (tham số override `CMD`, giống hệt
`docker compose run --rm <service> <module>` đang dùng thủ công).

**Environment lấy thẳng từ `docker-compose.yml`** qua
`docker compose --profile jobs config --format json` (subprocess, cache
`lru_cache`) — không hard-code lại từng biến môi trường trong DAG (tránh
lệch giữa 2 nơi định nghĩa, đúng tinh thần "1 nguồn sự thật" CLAUDE.md
nguyên tắc #3/#4 áp dụng sang cả tầng orchestration). **Tên image** thì
không lấy được từ `config` (service chỉ khai `build:`, không có `image:`,
`docker compose config` không tự sinh ra tên đã build) — dựng trực tiếp
`f"{COMPOSE_PROJECT_NAME}-{compose_service}:latest"`, đúng quy ước Compose
đặt tên khi build (`docker compose build` log "naming to ...
recommendation-mlops-extract-job").

### 2.3 5 DAG (`airflow/dags/`)

| File | Task (chuỗi `>>`) | Image |
|---|---|---|
| `dag_ingest.py` | `extract` | `extract-job` |
| `dag_transform.py` | `build_bronze >> build_silver >> build_gold` | `transform-job` |
| `dag_features_candidates.py` | `build_user_features` | `transform-job` |
| `dag_materialize.py` | `export_to_parquet >> feast_materialize` | `materialize-job` |
| `dag_training.py` | `train_sequence >> promote >> similar_items` | `training-job` |

Mỗi DAG `schedule=None`, `catchup=False` — trigger thủ công từ UI (đúng DoD
gốc, xem mục 4). `_defaults.py` giữ `DAG_KWARGS` dùng chung, không lặp lại
5 lần.

`promote` không cần nhận `run_id` từ `train_sequence` qua XCom:
`jobs/training/promote.py --version` mặc định là version MLflow mới nhất
đã đăng ký, và `train_sequence` luôn đăng ký version nó vừa train — mỗi
task tự đọc state MLflow, không có dữ liệu nào chảy qua Airflow (đúng
nguyên tắc #1: DAG không xử lý logic, chỉ gọi lại job).

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Lý do |
|---|---|---|
| `dag_maintenance.py` → `compact_iceberg.py` | **Không tạo** | `jobs/maintenance/compact_iceberg.py` chưa tồn tại — đã dời sang Phase 7 từ Phase 2 (`phase-2-lakehouse.md` mục "việc còn lại"). Phase 6 chỉ orchestrate job đã có, không viết job mới |
| `dag_features_candidates.py` gồm cả `similar_items` | `similar_items` chuyển sang cuối `dag_training.py` | `similar_items` đọc embedding từ model ở alias `production` — phải chạy *sau* khi `promote` xong, không phải trước lúc train. Đây là sửa lại nhóm task cho đúng phụ thuộc dữ liệu, không đổi nguyên tắc kiến trúc → không cần ADR |
| Mỗi image gắn tag `git rev-parse --short HEAD` (nguyên tắc #8) | Vẫn dùng tag `latest` | Project hiện **không phải git repository** — gap có sẵn từ Phase 0 (không service nào từ trước tới giờ gắn tag SHA), không phải phát sinh mới ở Phase 6. Sẽ áp dụng khi có git, có thể gộp cùng Phase 8 (CI/CD) |
| Không nêu cách DAG lấy env var của từng job | Đọc thẳng `docker compose config --format json` thay vì hard-code trong DAG | Tránh 1 nguồn sự thật bị nhân đôi (docker-compose.yml vs DAG code) — [[prefer-libraries-over-custom-code]] |

## 4. Definition of Done — đối chiếu

| Yêu cầu | Kết quả |
|---|---|
| Từ Airflow UI, trigger được `dag_ingest → dag_transform → dag_training → dag_materialize` theo đúng thứ tự phụ thuộc | ✅ — xem log trigger thủ công bên dưới |
| Mỗi task chạy trong container riêng | ✅ — `JobDockerOperator` mỗi lần chạy tạo 1 container job, `auto_remove="success"` tự xoá sau khi xong |
| Dừng ở task nào biết ngay job nào lỗi | ✅ — thử tắt `redis` trước khi trigger `dag_materialize`, task `feast_materialize` fail rõ ràng, `export_to_parquet` (không cần redis) vẫn pass |

## 5. Việc còn để lại cho phase sau

- Gắn tag `git rev-parse --short HEAD` cho mọi image (kể cả các phase
  trước) — cần `git init` trước, tự nhiên hợp với Phase 8 (CI/CD).
- `jobs/maintenance/compact_iceberg.py` + `dag_maintenance.py` — viết cùng
  lúc ở Phase 7 (đã dời từ Phase 2).
- Chưa có scheduling tự động (cron) hay cross-DAG trigger — DoD hiện tại
  chỉ yêu cầu trigger thủ công đúng thứ tự; đặt `schedule=` cụ thể là việc
  dễ, để lại vì chưa có yêu cầu tần suất chạy thật.

## 6. Danh sách file đã triển khai

### Airflow core

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `infra/docker/airflow/Dockerfile` | Image Airflow + Docker CLI/Compose plugin + provider `docker` | `AIRFLOW_IMAGE` build arg, constraints file theo đúng version Airflow |
| `requirements/airflow.txt` | Dependency riêng của Airflow | `apache-airflow-providers-docker==3.14.0` |
| `airflow/plugins/operators/job_docker_operator.py` | `JobDockerOperator` dùng chung mọi DAG | Đọc env qua `docker compose config`, tự dựng tên image `latest` |
| `airflow/dags/_defaults.py` | `DAG_KWARGS` dùng chung | `schedule=None`, `catchup=False` |
| `airflow/dags/dag_ingest.py` | Gọi `extract-job` | 1 task |
| `airflow/dags/dag_transform.py` | Bronze→Silver→Gold | 3 task chuỗi |
| `airflow/dags/dag_features_candidates.py` | `build_user_features` | 1 task |
| `airflow/dags/dag_materialize.py` | Export Parquet → Feast materialize | 2 task chuỗi |
| `airflow/dags/dag_training.py` | Train→Promote→Similar items | 3 task chuỗi |

### Hạ tầng

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `docker-compose.yml` | +4 service `airflow-*` | `LocalExecutor`, mount `docker.sock`, mount repo `:ro` tại `/opt/airflow/project` |
| `.env.example` | Biến môi trường Airflow | `AIRFLOW_PORT=8080`, `AIRFLOW_FERNET_KEY` (dev-only, cố định) |
| `Makefile` | `make airflow-up`/`airflow-down` | Tách khỏi `make up` |
