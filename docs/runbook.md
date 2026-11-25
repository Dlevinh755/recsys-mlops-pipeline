# Runbook

> Thao tác vận hành thường gặp. Lệnh chạy từ thư mục gốc repo, trừ
> khi ghi khác.

## Khởi động / dừng

```bash
make up            # hạ tầng core (Postgres, MinIO, Lakekeeper, MLflow,
                    # Redis, serving, ui) + Prometheus/Grafana (override
                    # tự nạp) + Airflow (tự lên cùng docker compose up)
docker compose --profile jobs build   # 5 image job (extract, transform, materialize,
                    # training, monitoring) — KHÔNG nằm trong make up; bắt buộc
                    # trên máy mới, lần đầu ≈ 15–20 phút
make airflow-up     # chỉ để build/khởi động riêng Airflow khi cần
docker compose -p mlops4rec-jenkins --env-file .env -f infra/jenkins/docker-compose.jenkins.yml up -d --build   # Jenkins (riêng)

make down           # dừng, giữ volume
make clean          # dừng + XOÁ volume (mất toàn bộ dữ liệu local)
```

## Máy mới / clone sạch

Thứ tự đã kiểm chứng 25–26/11/2026 (Windows + Docker Desktop, commit `c1ab91f` + sửa sau đó):

1. `cp .env.example .env` (tuỳ chọn thêm `COMPOSE_PROJECT_NAME=<tên>` để tách volume khỏi stack khác).
2. `docker compose up -d --build` (≈ 5–6 phút), rồi `bash scripts/smoke_test.sh` (Git Bash).
3. `docker compose --profile jobs build` (≈ 15–20 phút lần đầu).
4. Airflow http://localhost:8080 (`AIRFLOW_ADMIN_USER`/`AIRFLOW_ADMIN_PASSWORD` trong `.env`):
   trigger lần lượt `dag_ingest` → `dag_transform` → `dag_features_candidates` →
   `dag_materialize` → `dag_training` (≈ 0,5 / 0,6 / 0,4 / 0,8 / 8 phút; 8 phút vì 3 ứng viên).
5. Giám sát: `python scripts/upload_monitoring_baseline.py [--train-parquet ...]` rồi `dag_monitoring`.

Không có `make` (Windows) — tương đương, chạy từ thư mục gốc repo:

| `make` | Lệnh thay thế |
|---|---|
| `make up` | `docker compose up -d --build` |
| `make smoke` | `bash scripts/smoke_test.sh` |
| `make extract` | `bash scripts/run_job_locally.sh extract` |
| `make bronze` / `silver` / `gold` | `docker compose --profile jobs run --rm transform-job jobs.transform.build_bronze` (`build_silver`, `build_gold`) |
| `make user-features` | `docker compose --profile jobs run --rm transform-job jobs.features.build_user_features` |
| `make train` | `docker compose --profile jobs run --rm training-job jobs.training.train_agf` |
| `make promote` | `docker compose --profile jobs run --rm training-job jobs.training.promote` |
| `make similar-items` | `docker compose --profile jobs run --rm training-job jobs.candidates.similar_items` |
| `make airflow-up` | `docker compose build airflow-init airflow-webserver airflow-scheduler`, rồi `docker compose up -d airflow-db airflow-init airflow-webserver airflow-scheduler` |
| `make down` | `docker compose down --remove-orphans` |
| `make clean` | `docker compose down --volumes --remove-orphans`, xoá `data/dlt-pipelines/reco_extract_*` |

`bash` trong PowerShell có thể trỏ vào WSL chưa cài distro (`execvpe(/bin/bash) failed`): dùng
Git Bash, hoặc `& "C:\Program Files\Git\bin\bash.exe" scripts/smoke_test.sh`.

## Pipeline thủ công (không qua Airflow)

Đúng thứ tự phụ thuộc — xem `README.md` mục "Chạy Phase N" để có lệnh đầy
đủ từng bước:

```
extract → build_bronze → build_silver → build_gold → build_user_features
  → { export_to_parquet → feast_materialize , train_agf → promote
      → similar_items }
```

## Chạy DAG bằng image của CI

Mặc định (`IMAGE_TAG=dev`) Airflow chạy image build local. Để chạy đúng image
Jenkins đã build và đẩy lên Docker Hub cho một commit (tag là git SHA ngắn,
xem log build hoặc `git rev-parse --short HEAD`):

```powershell
# 1. .env: DOCKERHUB_USERNAME=<tên-docker-hub>  và  IMAGE_TAG=<sha>
docker compose --profile jobs pull extract-job transform-job materialize-job training-job monitoring-job
docker compose restart airflow-scheduler
```

Restart scheduler vì `JobDockerOperator` cache `docker compose config` theo
tiến trình. Kiểm tra: log task có dòng `Starting docker container from image
<tên>/mlops4rec-<x>:<sha>`. Quay lại bản local: đặt `IMAGE_TAG=dev`, restart
scheduler. Image `serving`/`ui`/`airflow` không do DAG chạy; các service đó
đổi tag khi `docker compose up -d` với `IMAGE_TAG` mới.

## Kiểm tra quyết định promote không đổi alias

```powershell
docker compose --profile jobs run --rm --no-deps training-job jobs.training.promote --dry-run
```

In JSON có `comparison` (`same_split` / `legacy` / `first`), `reason`, `would_promote`; alias `production` giữ nguyên. `--version N` xét version cụ thể, `--min-delta X` đòi challenger vượt champion hơn X. `train_agf --no-champion` bỏ bước chấm champion (khi đó cổng rơi về `legacy`). Xem ADR 0013.

## Vòng lặp drift → retrain (ADR 0014)

Chạy tay từng bước, không qua Airflow (exit `0` = retrain, `99` = bỏ qua):

```powershell
docker compose --profile jobs run --rm --no-deps monitoring-job jobs.monitoring.data_drift_report
docker compose --profile jobs run --rm --no-deps monitoring-job jobs.monitoring.retrain_decision
docker compose --profile jobs run --rm --no-deps monitoring-job jobs.monitoring.refresh_baseline
```

Quyết định và lý do nằm ở `monitoring-reports/retrain_decision/<ts>.json`. Chỉnh ngưỡng
bằng `RETRAIN_*` trong `.env` (xem `.env.example`); demo thì `RETRAIN_COOLDOWN_HOURS=0`,
rồi `docker compose restart airflow-scheduler`. Chạy cả chuỗi: unpause
`dag_continuous_training` (tạo ở trạng thái tạm dừng) và Trigger; unpause cũng tạo
ngay một run cho khoảng trước. Đặt lại baseline về `train.parquet`: xoá
`current.parquet` và `current.meta.json` trong bucket `monitoring-baseline`.

## Train nhiều ứng viên (ADR 0015)

```powershell
docker compose --profile jobs run --rm training-job jobs.training.train_agf --candidates gru4rec,sasrec,gru4rec_sum
```

Id hợp lệ: `gru4rec` (cấu hình gốc, mốc so sánh), `sasrec`, `gru4rec_sum`, `gru4rec_idonly`. Từ ADR 0016, `docker compose` và `.env.example` mặc định chạy ba ứng viên `gru4rec,sasrec,gru4rec_sum` (lệnh trên chỉ để ghi tường minh). Mỗi ứng viên là
một run MLflow cùng `candidate_group`; chỉ người thắng được đăng ký (đọc `selected_from`,
`candidate_<id>_val_ndcg_at_10` ở run đó, `candidate_selected` ở mọi run). Qua Airflow:
đặt `TRAIN_CANDIDATES=...` trong `.env` rồi `docker compose restart airflow-scheduler`;
đặt `TRAIN_CANDIDATES=gru4rec` để về một ứng viên. Mỗi ứng viên thêm ~2 phút (mặc định 3 ứng viên ≈ 6–7 phút).

## Rollback model thủ công

Model production được chọn qua alias MLflow (`production`), không phải
"bản mới nhất" — rollback là đổi alias trỏ về version cũ, **không cần** xoá
hay build lại gì:

```bash
docker compose --profile jobs run --rm --entrypoint python training-job -c "
from reco_mlops_libs.mlflow_utils.registry_client import get_client, set_production_version
set_production_version('reco-mlops-agf', '<version cũ muốn rollback về>', client=get_client())
"
```

`serving` tự nhận version mới trong vòng `MODEL_POLL_INTERVAL_SECONDS`
(mặc định 60s) — **không cần restart** `serving`. Kiểm tra đã áp dụng:

```bash
curl http://localhost:8000/health   # {"status":"ok","model_version":"<version>"}
```

Nếu cần rollback ngay lập tức (không đợi poll interval), restart `serving`
để nó load lại từ đầu:

```bash
docker compose restart serving
```

## Sự cố hay gặp

| Triệu chứng | Nguyên nhân thường gặp | Xử lý |
|---|---|---|
| `docker compose` báo lỗi port đã dùng | Container CI (`reco-ci-*`) hoặc stack dev cũ còn chạy song song | `docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.ci.yml -p <project cũ> down --volumes` |
| `serving` trả `source: "fallback"` mãi không đổi | Chưa có model ở alias `production`, hoặc user không có `gold.user_features`/Redis `recent_items` | `make train && make promote`; xem `docs/modules/phase-5-serving.md` mục 3 nhánh DoD |
| `dag_ingest` chạy `success` nhưng 0 dòng mới | Đúng hành vi — dlt incremental không có gì mới thì trả 0 dòng (ADR 0006), không phải lỗi | `python scripts/simulate_incremental_update.py` để tạo dữ liệu mới rồi chạy lại |
| Airflow task fail `DockerContainerFailedException` | Container job thật lỗi (xem log ngay trong Airflow UI, `DockerOperator` stream log về) | Xem `docker logs` hoặc log task trên UI, sửa job, `airflow tasks clear` rồi trigger lại |
| Jenkins `Build & Push Images` fail, các stage khác pass | `dockerhub-creds` chưa có Access Token thật (mặc định để trống) | Set `DOCKERHUB_TOKEN` trong `.env`, restart Jenkins container để JCasC nạp lại |
| Ruff/pytest báo lỗi cache "Read-only file system" khi lint qua bind-mount | Mount `:ro` không ghi cache được | Dùng `ruff check --no-cache` (đã áp dụng trong `Jenkinsfile`) |
| Job transform (`build_bronze/silver/gold`) fail `Access Denied` khi đọc/ghi MinIO, dù catalog tạo bảng bình thường | Lakekeeper dùng remote signing và trả `s3.signer.uri` là `http://localhost:8181/catalog/`; trong container job, `localhost` không phải Lakekeeper nên chữ ký S3 không lấy được | Đặt `LAKEKEEPER__BASE_URI=http://lakekeeper:8181/` trong `infra/iceberg-catalog/catalog.env`, rồi `docker compose up -d --force-recreate lakekeeper`. Kiểm tra: `curl.exe -s http://localhost:8181/catalog/v1/<warehouse-id>/namespaces/silver/tables/products` phải có `"s3.signer.uri":"http://lakekeeper:8181/catalog/"` (đã xác nhận với Lakekeeper v0.12.1) |
| `docker compose up` báo `minio-init ... exit 2`, log `set: -: invalid option` | Script `.sh` bị đổi sang CRLF khi clone trên Windows (`core.autocrlf=true`) — repo thiếu `.gitattributes` trước commit `105736e` | `git pull` lấy `.gitattributes`, rồi `git add --renormalize . && git checkout -- .` và `docker compose up -d` lại |
| DAG fail vì không thấy image `local/mlops4rec-<job>:dev` hoặc chạy mã cũ (vd. `dag_training` chỉ ra 1 run dù `TRAIN_CANDIDATES` có 3) | Image job (profile `jobs`) không được `docker compose up` build; còn lại image cũ hoặc không có | `docker compose --profile jobs build`, rồi `docker compose restart airflow-scheduler` |
| `dag_monitoring` / `data_drift_report` fail `FileNotFoundError: monitoring-baseline/train.parquet` | Chưa nạp baseline giám sát | `python scripts/upload_monitoring_baseline.py` (thêm `--train-parquet` nếu `amazone_data` không cạnh repo) |
| Silver lệch Bronze (thiếu/thừa dòng), vừa đổi schema hoặc `sql/silver/*.sql`, hoặc `build_silver` báo `Target table has duplicate rows, aborting upsert` | Silver tăng dần chỉ xử lý Bronze nạp sau watermark `reco.silver.watermark_ingested_at` (ADR 0010); đổi schema/SQL không tự áp dụng lại cho dòng cũ, và lần `build_bronze` chạy chồng nhau có thể để sót dòng dưới watermark | Tính lại toàn bộ Silver: `docker compose --profile jobs run --rm transform-job jobs.transform.build_silver --full-refresh` (chạy lại `build_gold` sau đó). Kiểm tra: kết quả JSON có `"mode": "full"`, rồi lần chạy kế tiếp trả `"mode": "incremental"` với `bronze_rows_scanned` = 0 |

## Reset hoàn toàn (cold start)

```bash
make clean          # xoá volume core + state extract của dlt (data/dlt-pipelines/)
make airflow-down   # (nếu đang chạy)
make up             # Airflow tự lên cùng; image job còn nguyên (không bị clean)
```

Máy chưa từng build image job thì chạy thêm `docker compose --profile jobs build` (xem mục
"Máy mới / clone sạch"); sau khi reset, bucket `monitoring-baseline` cũng mất nên nạp lại
baseline bằng `scripts/upload_monitoring_baseline.py`.

`data/dlt-pipelines/` là bind mount trên máy host, **không** nằm trong Docker
volume. Nếu còn state cũ ở đó thì `last_value` của extract vẫn là mốc của lần
chạy trước, và dlt chỉ khôi phục từ bucket khi state cục bộ không có — dòng
nguồn mới có `updated_at` thấp hơn mốc đó sẽ bị bỏ qua mà không báo lỗi
(`phase-10-agf.md` mục 7.3). Reset thủ công không qua `make clean` thì xoá
`data/dlt-pipelines/reco_extract_*` **và** bucket `extract-staging` cùng lúc.

Sau đó chạy lại toàn bộ pipeline thủ công hoặc qua Airflow từ đầu. Dữ liệu
Amazon seed (`infra/source-db/init/seed_data_amazon.sql`) tự nạp lại nhờ
`docker-entrypoint-initdb.d` — không cần seed tay.
