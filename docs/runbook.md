# Runbook

> Thao tác vận hành thường gặp. Lệnh chạy từ thư mục gốc repo, trừ
> khi ghi khác.

## Khởi động / dừng

```bash
make up            # hạ tầng core (Postgres, MinIO, Lakekeeper, MLflow,
                    # Redis, serving, ui) + Prometheus/Grafana (override
                    # tự nạp)
make airflow-up     # Airflow (riêng, không thuộc make up)
docker compose -f infra/jenkins/docker-compose.jenkins.yml up -d --build   # Jenkins (riêng)

make down           # dừng, giữ volume
make clean          # dừng + XOÁ volume (mất toàn bộ dữ liệu local)
```

## Pipeline thủ công (không qua Airflow)

Đúng thứ tự phụ thuộc — xem `README.md` mục "Chạy Phase N" để có lệnh đầy
đủ từng bước:

```
extract → build_bronze → build_silver → build_gold → build_user_features
  → { export_to_parquet → feast_materialize , train_agf → promote
      → similar_items }
```

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

## Reset hoàn toàn (cold start)

```bash
make clean          # xoá volume core
make airflow-down   # (nếu đang chạy)
make up
make airflow-up
```

Sau đó chạy lại toàn bộ pipeline thủ công hoặc qua Airflow từ đầu. Dữ liệu
Amazon seed (`infra/source-db/init/seed_data_amazon.sql`) tự nạp lại nhờ
`docker-entrypoint-initdb.d` — không cần seed tay.
