# Cấu trúc thư mục project — Recommendation MLOps (MVP: DuckDB + PyIceberg + Polars)

Cấu trúc bên dưới bám theo các nguyên tắc đã chốt trong đề xuất kiến trúc:
Airflow chỉ orchestration, mỗi job có image/command độc lập, code+SQL đóng vào
image (không bind mount), Iceberg là source of truth, schema contract rõ ràng,
training không phụ thuộc engine sinh dataset. Bổ sung mới: lớp tín hiệu tương
tác tức thời ở serving (`/interact` + re-rank), giao diện demo (`ui/`), và
công cụ kiểm thử tải (`tests/load/`) — xem giải thích ở cuối tài liệu.

```text
recommendation-mlops/
├── README.md
├── Makefile
├── docker-compose.yml
├── docker-compose.override.yml
├── .env.example
├── .gitignore
├── .dockerignore
├── .pre-commit-config.yaml            # black/ruff/isort chạy trước commit
├── .github/
│   └── workflows/
│       ├── ci.yml                     # lint, unit test, integration test,
│       │                              #   model-smoke-test (job riêng)
│       ├── model-cd.yml               # trigger MLflow promote -> ping serving reload
│       └── build-push-images.yml      # build + push khi merge main
│
├── infra/
│   ├── docker/
│   │   ├── extract/Dockerfile
│   │   ├── transform/Dockerfile
│   │   ├── training/Dockerfile
│   │   ├── serving/Dockerfile
│   │   ├── ui/Dockerfile              # MỚI — image Streamlit, tách khỏi serving
│   │   ├── monitoring/Dockerfile      # job Evidently
│   │   └── airflow/Dockerfile
│   ├── minio/
│   │   └── init-buckets.sh
│   ├── iceberg-catalog/
│   │   └── catalog.env                # REST catalog config
│   ├── mlflow/
│   │   ├── docker-compose.mlflow.yml  # tracking server riêng
│   │   └── mlflow.env                 # backend store (Postgres) + artifact root (MinIO)
│   ├── source-db/                     # DB nguồn GIẢ LẬP cho extract (không phải lakehouse)
│   │   ├── docker-compose.source-db.yml
│   │   └── init/
│   │       ├── schema.sql             # bảng có created_at/updated_at
│   │       └── seed_data.sql
│   ├── monitoring/
│   │   ├── prometheus/
│   │   │   └── prometheus.yml         # scrape config: serving, airflow, redis
│   │   └── grafana/
│   │       ├── provisioning/
│   │       │   ├── datasources/prometheus.yml
│   │       │   └── dashboards/dashboards.yml
│   │       └── dashboards/
│   │           └── serving-overview.json   # latency p95, cache hit, error rate
│   └── k8s/                           # để dành cho giai đoạn sau
│       └── helm/
│
├── airflow/
│   ├── dags/
│   │   ├── dag_ingest.py              # gọi: python -m jobs.extract.run
│   │   ├── dag_transform.py           # gọi: jobs.transform.build_silver/build_gold
│   │   ├── dag_features_candidates.py
│   │   ├── dag_training.py
│   │   ├── dag_materialize.py         # export -> Feast -> Redis (+ similar_items -> Redis)
│   │   ├── dag_monitoring.py          # Evidently drift/quality report định kỳ
│   │   └── dag_maintenance.py         # compact/expire snapshot Iceberg
│   └── plugins/
│       └── operators/
│           └── job_docker_operator.py # wrapper DockerOperator dùng chung mọi DAG
│
├── jobs/                              # BUSINESS LOGIC — mỗi package đóng vào 1 image
│   ├── extract/
│   │   ├── run.py
│   │   ├── sources/
│   │   │   ├── interactions.py        # đọc cả dữ liệu seed lẫn dữ liệu ghi qua /interact
│   │   │   └── products.py
│   │   └── watermark.py               # last_watermark / current_watermark
│   ├── transform/
│   │   ├── build_bronze.py
│   │   ├── build_silver.py
│   │   ├── build_gold.py
│   │   └── iceberg_writer.py          # wrapper PyIceberg commit dùng chung
│   ├── features/
│   │   ├── build_user_features.py
│   │   ├── build_item_features.py
│   │   └── build_interaction_features.py
│   ├── candidates/
│   │   ├── similar_items.py           # kết quả được đẩy sang Redis, dùng cho cả
│   │   │                              #   trang "sản phẩm tương tự" lẫn recency_boost.py
│   │   └── popular_items.py
│   ├── training/
│   │   ├── build_training_set.py      # temporal split + group theo user/query
│   │   ├── negative_sampling.py
│   │   ├── train_ranker.py            # LightGBMRanker + log MLflow, implement
│   │   │                              #   interface ranking/base.py
│   │   ├── evaluate.py                # NDCG/Recall/MAP/Coverage — dùng chung
│   │   │                              #   cho cả LightGBM lẫn sequence model
│   │   ├── promote.py                 # promotion gate
│   │   ├── build_sequence_dataset.py  # MỞ RỘNG (Phase 9) — sinh chuỗi
│   │   │                              #   item_id theo user từ Gold layer
│   │   └── train_sequence.py          # MỞ RỘNG (Phase 9) — mini-GRU4Rec
│   │                                  #   (PyTorch), tag model_type=sequence,
│   │                                  #   implement cùng interface base.py
│   ├── materialize/
│   │   ├── export_to_parquet.py       # Iceberg -> Parquet snapshot
│   │   ├── feast_materialize.py       # offline store -> Redis (online feature)
│   │   └── export_similar_items_to_redis.py  # MỚI — similar_items -> Redis,
│   │                                  #   key riêng (similar_items:{item_id}),
│   │                                  #   tách khỏi namespace feature của Feast
│   ├── monitoring/
│   │   ├── data_drift_report.py       # Evidently: so Silver hôm nay vs baseline
│   │   ├── model_quality_report.py    # Evidently: prediction drift, feature drift
│   │   └── report_sink.py             # ghi HTML/JSON report lên MinIO
│   └── maintenance/
│       └── compact_iceberg.py
│
├── sql/                               # SQL thuần, tách khỏi code orchestration
│   ├── bronze/
│   ├── silver/
│   ├── gold/
│   └── features/
│
├── serving/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── homepage.py            # /recommend/homepage
│   │   │   ├── similar.py             # /recommend/similar
│   │   │   └── interact.py            # MỚI — POST /interact: ghi Redis (A) +
│   │   │                              #   ghi source-db (B), xem báo cáo mục 8.1
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── model_loader.py        # poll MLflow registry định kỳ, tự
│   │   │   │                          #   chuyển sang model "production" mới
│   │   │   ├── cache.py               # Redis cache check
│   │   │   ├── fallback.py            # fallback popular-items
│   │   │   ├── recency_boost.py       # MỚI — đọc recent_items:{user_id} từ
│   │   │   │                          #   Redis, re-rank bằng similar_items
│   │   │   ├── source_db_writer.py    # MỚI — ghi 1 dòng vào bảng interactions
│   │   │   │                          #   của source-db (nhánh B của /interact)
│   │   │   └── metrics.py             # prometheus-fastapi-instrumentator
│   │   └── schemas/
│   └── tests/
│       └── test_model_reload.py       # xác nhận serving nhận model version mới
│
├── ui/                                 # MỚI — giao diện demo, KHÔNG thuộc luồng
│   │                                   #   nghiệp vụ chính (xem báo cáo mục 8.2)
│   ├── app.py                          # Streamlit: danh sách gợi ý + nút "Đã mua"
│   └── requirements-ui.txt
│
├── feature_repo/                      # Feast repo riêng (theo chuẩn Feast)
│   ├── feature_store.yaml
│   ├── entities.py
│   ├── features_user.py
│   ├── features_item.py
│   └── data_sources.py                # FileSource trỏ Parquet snapshot
│
├── libs/                              # package nội bộ, cài bằng pip vào từng image
│   ├── pyproject.toml                 # tên package: reco-mlops-libs, có version số
│   ├── src/
│   │   └── reco_mlops_libs/
│   │       ├── common/
│   │       │   ├── config.py          # đọc biến môi trường (MINIO_ENDPOINT, ...)
│   │       │   ├── logging.py
│   │       │   └── env.py
│   │       ├── iceberg/
│   │       │   ├── catalog.py         # kết nối REST Catalog dùng chung
│   │       │   ├── validators.py      # pandera: validate DataFrame trước khi commit
│   │       │   └── schema_contracts/  # định nghĩa schema mỗi bảng — dùng ở
│   │       │       ├── bronze_interactions.py #   cả job DuckDB lẫn job Spark sau này
│   │       │       ├── silver_interactions.py
│   │       │       └── gold_item_features.py
│   │       ├── mlflow_utils/
│   │       │   ├── registry_client.py # get_production_version(), dùng chung
│   │       │   │                      #   cho cả promote.py và model_loader.py
│   │       │   └── logging_helpers.py # log_param snapshot_id, metric chuẩn hoá
│   │       └── ranking/
│   │           └── base.py            # MỚI — interface mỏng dùng chung:
│   │                                  #   predict(user_id, candidate_items)
│   │                                  #   -> scores; LightGBM (Phase 4) và
│   │                                  #   sequence model (Phase 9, mở rộng)
│   │                                  #   đều implement interface này
│   └── CHANGELOG.md                   # ghi lại mỗi lần đổi version package
│
├── requirements/                      # dependency tách theo image, Dockerfile COPY đúng file cần
│   ├── base.txt                       # chung: pydantic, polars, pyiceberg... + "-e ../libs"
│   ├── extract.txt
│   ├── transform.txt
│   ├── training.txt                   # lightgbm, mlflow... + torch (CPU build,
│   │                                  #   chỉ cần khi làm Phase 9 mở rộng)
│   ├── serving.txt
│   ├── ui.txt                         # MỚI — streamlit, requests
│   ├── monitoring.txt
│   └── dev.txt                        # pytest, ruff, black, pre-commit, locust
│
├── notebooks/                         # exploration, KHÔNG chạy trong pipeline
│
├── tests/
│   ├── unit/                          # 1 thư mục con / job
│   ├── integration/                   # test với MinIO + DuckDB thật (docker)
│   ├── data_quality/                  # test Evidently / schema contract
│   │   └── test_train_serve_skew.py   # so feature offline (Gold) vs online (Redis)
│   ├── model/
│   │   ├── test_training_smoke.py     # train_ranker.py chạy trên sample_data, không lỗi
│   │   └── sample_data/               # vài trăm dòng, commit sẵn trong repo — CHỈ để
│   │       └── interactions_sample.parquet  #   smoke test, không đánh giá chất lượng model
│   ├── load/                          # MỚI — không thuộc CI, chạy thủ công trước demo
│   │   └── locustfile.py              # gọi /recommend/homepage, /recommend/similar
│   └── serving/
│       └── test_model_reload.py       # xác nhận serving nhận model version mới
│
├── scripts/
│   ├── seed_local_data.sh
│   ├── seed_source_db.py              # load 1 phần dataset Amazon vào source-db
│   ├── simulate_incremental_update.py # insert/update lô mới -> demo watermark
│   └── run_job_locally.sh             # chạy 1 job ngoài Airflow để debug
│
└── docs/
    ├── architecture.md
    ├── runbook.md
    ├── data_dictionary.md             # mô tả schema Bronze/Silver/Gold
    └── model_card.md                  # mục tiêu, feature, giới hạn của ranking model
```

## Vì sao chia như vậy

**`jobs/` tách khỏi `airflow/dags/`.** DAG chỉ gọi `python -m jobs.xxx.run`, không
chứa business logic — đúng nguyên tắc "Airflow chỉ orchestration". Nhờ vậy bạn
test được từng job bằng cách chạy trực tiếp (`scripts/run_job_locally.sh`) mà
không cần khởi động Airflow.

**`sql/` tách khỏi `jobs/`.** SQL thuần (không phụ thuộc DuckDB-specific
syntax nếu tránh được) giúp việc migrate sang Spark sau này chỉ cần đổi engine
đọc SQL, không viết lại logic — đúng mục "Ưu tiên SQL chuẩn, tách SQL khỏi code
orchestration" trong đề xuất.

**`libs/iceberg/schema_contracts/` là điểm mấu chốt.** Đây là nơi định nghĩa
"input/output table rõ ràng" cho từng job. Cả job DuckDB hiện tại lẫn job
Spark sau này đều import cùng contract này, tránh lệch schema giữa hai engine.

**Mỗi thư mục dưới `jobs/` map 1-1 với 1 Dockerfile dưới `infra/docker/`.**
`extract/`, `transform/` (gồm cả `features/`, `candidates/` — build chung vào
image transform vì cùng chạy DuckDB/Polars), `training/`, `serving/`. Image
build bằng `COPY jobs/ /app/jobs`, `COPY sql/ /app/sql`, `COPY libs/ /app/libs`
— không bind mount, đúng nguyên tắc "code và SQL nên được đóng vào image".

**`feature_repo/` để riêng ở root**, không lồng trong `jobs/`, vì Feast có yêu
cầu cấu trúc repo riêng của nó (feature_store.yaml ở root repo) — để lẫn vào
jobs sẽ khó chạy `feast apply`/`feast materialize` đúng chuẩn.

**`tests/integration/` chạy với MinIO + DuckDB thật qua docker-compose test**,
không mock — vì phần rủi ro lớn nhất (PyIceberg commit, REST Catalog) chỉ lộ ra
khi chạy thật, nên nên có test tầng này sớm (POC đã trao đổi ở lần trước).

**`infra/monitoring/` tách riêng khỏi `docker-compose.yml` chính**, nên khai
báo Prometheus + Grafana trong `docker-compose.override.yml` (đã có sẵn ở
root). Cách này cho phép chạy pipeline nhẹ khi chỉ cần debug job (`docker
compose up`), và bật thêm monitoring khi cần demo/đo latency
(`docker compose -f docker-compose.yml -f docker-compose.override.yml up`).
Prometheus scrape `/metrics` từ `serving/` (qua `metrics.py`) — dùng thư viện
`prometheus-fastapi-instrumentator`, gần như không cần code thêm. Grafana đọc
dashboard JSON provision sẵn trong `grafana/dashboards/`, nên khi demo không
cần dựng dashboard bằng tay — dashboard tự có ngay khi container khởi động.
Prometheus/Grafana chỉ đo metric vận hành (latency, request rate, cache hit);
việc theo dõi data/model drift vẫn do Evidently AI đảm nhiệm như đã thiết kế —
hai công cụ không chồng lấn.

**`infra/source-db/` là DB nguồn giả lập, tách biệt hoàn toàn khỏi lakehouse.**
Đây không phải Bronze/Silver/Gold — nó đóng vai trò "hệ thống OLTP bên ngoài"
mà job `extract` sẽ đọc từ đó, giống một hệ thống thật sẽ có. `seed_source_db.py`
load dữ liệu ban đầu, `simulate_incremental_update.py` insert/update thêm bản
ghi có `updated_at` mới mỗi lần chạy — nhờ vậy bạn demo được cơ chế watermark
hoạt động thật (không phải chỉ lý thuyết trong báo cáo). `serving/app/core/
source_db_writer.py` (mới) ghi vào đúng bảng này theo cùng cơ chế, chỉ khác
nguồn gốc — một bên là script giả lập, một bên là hành động thật của người
dùng qua `/interact`.

**`infra/mlflow/` tách thành service riêng**, có backend store (Postgres) và
artifact root trỏ về MinIO — không dùng chung Postgres với `source-db` để
tránh lẫn lộn giữa "dữ liệu nghiệp vụ giả lập" và "metadata thật của hệ thống
MLOps".

**`jobs/monitoring/` + `dag_monitoring.py`** hiện thực hoá phần Evidently AI
đã nêu trong đề cương nhưng trước đó chưa có chỗ trong cấu trúc thư mục. Job
này so sánh phân phối dữ liệu Silver hôm nay với baseline, sinh report
HTML/JSON, đẩy lên MinIO — tách bạch với Prometheus/Grafana (đo vận hành) như
đã giải thích ở trên.

**`libs/` được đóng gói thành package cài được (`reco_mlops_libs`), không COPY thô.**
Trước đây mỗi Dockerfile `COPY libs/` trực tiếp vào image — rủi ro là khi sửa
1 dòng trong `catalog.py`, phải nhớ rebuild **cả 5 image**, rất dễ quên và
dẫn tới tình trạng image `training` chạy code cũ trong khi `transform` đã
chạy bản mới, lỗi khó phát hiện vì không báo lỗi rõ ràng. Cách khắc phục:
`libs/pyproject.toml` định nghĩa package `reco_mlops_libs` có version số
tường minh (`CHANGELOG.md` ghi lại mỗi lần tăng version). Mỗi
`requirements/<job>.txt` khai `-e ../libs` (hoặc pin version cụ thể khi build
production image thật). Nhờ vậy: (1) mọi image biết chính xác đang chạy
version nào của code dùng chung — không còn "COPY thô, ai đoán được version
nào đang chạy ở đâu"; (2) CI có thể chạy `pytest libs/` độc lập như một
package riêng, không phụ thuộc job nào; (3) khi thêm job mới, chỉ cần khai
`reco_mlops_libs` trong requirements của job đó, không cần sửa Dockerfile
copy logic.

**`requirements/` tách theo từng image** thay vì 1 file duy nhất — mỗi
Dockerfile chỉ `COPY requirements/<job>.txt` + `requirements/base.txt`, giúp
image nhẹ hơn (không cài lightgbm/mlflow vào image serving, không cài fastapi
vào image training) và build cache hiệu quả hơn. `requirements/ui.txt` (mới)
tách riêng vì `ui/` không đóng vai trò trong pipeline batch, không nên lẫn
dependency Streamlit vào bất kỳ image nghiệp vụ nào.

**CI/CD cho model tách biệt khỏi CI/CD cho code/image.** `ci.yml` có thêm job
`model-smoke-test` — chạy `train_ranker.py` + `evaluate.py` trên
`tests/model/sample_data/` (vài trăm dòng, commit sẵn trong repo, cố định
seed). Mục đích chỉ để xác nhận pipeline training không lỗi cú pháp/logic khi
có PR thay đổi code — **không** dùng để đánh giá chất lượng model thật (tập
mẫu quá nhỏ để có ý nghĩa thống kê). Việc đánh giá chất lượng thật vẫn do
`dag_training.py` → `evaluate.py` → `promote.py` đảm nhiệm trên dữ liệu đầy đủ.

Về CD: trước đây `promote.py` chỉ đánh dấu model "production" trong MLflow
registry, nhưng `model_loader.py` chỉ load model một lần lúc container khởi
động — nghĩa là model mới không bao giờ được dùng cho tới khi có người restart
serving thủ công. Sửa lại: `model_loader.py` poll registry định kỳ (qua
`libs/mlflow_utils/registry_client.py`, dùng chung với `promote.py` để tránh
lặp logic truy vấn registry) — khi phát hiện version "production" thay đổi,
tự động load model mới mà không cần redeploy. `model-cd.yml` chỉ đóng vai trò
thông báo/ping (không bắt buộc), vì cơ chế polling ở serving đã tự đủ để
"đóng vòng lặp" mà không cần một workflow CD phức tạp.

**Không có thư mục `data/` hay `models/` lưu file cục bộ lâu dài** — mọi thứ đi
qua MinIO/Iceberg/MLflow artifact store/Redis, đúng nguyên tắc "không trao đổi
dữ liệu qua local filesystem giữa container".

### `/interact`, `recency_boost.py` và `ui/` — vì sao đặt ở đây

**`serving/app/api/interact.py` + `core/source_db_writer.py` + `core/
recency_boost.py` nằm trong `serving/`, không phải một job riêng trong
`jobs/`.** Khác với các job batch (chạy theo lịch, không có người chờ), đây
là logic phục vụ **một request đơn lẻ, cần trả lời ngay** — đúng bản chất của
tầng serving, không phù hợp mô hình DockerOperator/batch. `source_db_writer.py`
tách thành module riêng (không gộp vào `interact.py`) để dễ test độc lập việc
ghi vào source-db, và để `recency_boost.py` có thể tái sử dụng logic đọc
Redis mà không phụ thuộc ngược vào tầng API.

**`jobs/materialize/export_similar_items_to_redis.py` là mảnh nối giữa batch
và serving.** `similar_items.py` (đã có sẵn từ trước) tính candidate theo
batch; job mới này chỉ thêm bước đẩy kết quả đó sang Redis với tiền tố key
riêng (`similar_items:{item_id}`), để `recency_boost.py` tra cứu được với độ
trễ thấp mà không phải tính lại lúc request tới.

**`ui/` đặt ngang hàng với `serving/`, không lồng bên trong.** Đây là ứng
dụng độc lập, gọi HTTP vào `serving/` như một client bên ngoài — không chia
sẻ code, không chia sẻ image, để rõ ràng rằng UI có thể bị bỏ đi hoàn toàn mà
không ảnh hưởng bất kỳ phần nào của pipeline nghiệp vụ. `infra/docker/ui/
Dockerfile` và `requirements/ui.txt` vì vậy cũng tách riêng.

**`tests/load/` không nằm trong CI (`ci.yml`).** Locust dùng để tạo traffic
trình diễn (mục 3.2 báo cáo kỹ thuật), chạy thủ công trước buổi demo/bảo vệ —
đưa vào CI tự động sẽ chỉ làm chậm pipeline CI mà không mang lại giá trị kiểm
tra chất lượng code.

### `libs/.../ranking/base.py` và `build_sequence_dataset.py`/`train_sequence.py` — vì sao tách riêng

**`ranking/base.py` được thêm ở Phase 4 (MVP1 chính), không phải Phase 9.**
Đây là điểm cố ý: interface `predict(user_id, candidate_items) -> scores`
không lộ ra ngoài việc model cụ thể (LightGBM) cần input dạng gì (DataFrame
phẳng). `model_loader.py`, `homepage.py`, `similar.py` chỉ gọi qua interface
này — không bao giờ gọi thẳng API của LightGBM. Chi phí thêm interface này
ở Phase 4 rất nhỏ (một class trừu tượng vài chục dòng), nhưng nếu bỏ qua và
để tầng API gọi trực tiếp `model.predict(dataframe)`, sau này muốn thêm model
khác kiểu input (như sequence model ở Phase 9, nhận chuỗi tensor thay vì
DataFrame) sẽ phải sửa lại đồng thời `model_loader.py`, `homepage.py`,
`similar.py` — rủi ro và tốn công hơn nhiều so với làm đúng từ đầu.

**`build_sequence_dataset.py` và `train_sequence.py` nằm trong cùng
`jobs/training/`**, không tách package riêng — vì vẫn đọc từ Gold layer
(Iceberg) như `build_training_set.py`, chỉ khác cách tổ chức dữ liệu đầu ra
(chuỗi thay vì bảng phẳng) và thuật toán train. Gộp chung giữ nguyên nguyên
tắc "1 thư mục `jobs/` map 1 Dockerfile" — `train_sequence.py` build vào cùng
image `training` (chỉ thêm `torch` vào `requirements/training.txt`), không
cần thêm Dockerfile mới.

**Hai file này được đánh dấu MỞ RỘNG (Phase 9)** trong cây thư mục vì thuộc
nhánh tuỳ chọn, không nằm trên đường găng của MVP1 — chi tiết lý do tách
phase và Definition of Done nằm trong `de-xuat-trien-khai.md`.

## Rà soát: project đã đủ cho một hệ MLOps hoàn chỉnh chưa?

Đối chiếu với vòng đời MLOps chuẩn (data → feature → train → registry →
serve → monitor → feedback loop), sau khi thêm các phần trên thì đã che phủ
gần đủ. Vẫn còn vài chỗ đáng cân nhắc, chia theo mức ưu tiên:

**Nên có (ảnh hưởng trực tiếp đến việc bảo vệ/đánh giá):**
- `docs/model_card.md` và `docs/data_dictionary.md` — hội đồng chấm khóa luận
  thường hỏi "feature nào ảnh hưởng nhất", "giới hạn của model là gì". Có sẵn
  tài liệu này giúp bạn trả lời nhất quán với những gì đã code, thay vì trả
  lời ứng biến. (đã thêm ở trên)
- `libs/iceberg/validators.py` (pandera/pydantic) — hiện `schema_contracts/`
  mới là khai báo, chưa có chỗ **enforce** schema trước khi commit Iceberg.
  Không có bước validate, một job lỗi có thể ghi sai schema vào bảng mà không
  ai biết cho tới khi job sau đó fail. (đã thêm ở trên)
- **CI/CD cho model** — CI: `model-smoke-test` chạy trên sample data để bắt
  lỗi pipeline training trước khi merge; CD: `model_loader.py` poll registry
  để tự nhận model mới sau khi `promote.py` chạy, không cần restart thủ công.
  (đã thêm ở trên)
- **Data-to-model lineage** — `train_ranker.py` nên log `iceberg_snapshot_id`
  của dữ liệu dùng để train vào MLflow (qua `logging_helpers.py`), để mỗi
  model version truy vết được chính xác đã train trên dữ liệu nào — gần như
  miễn phí vì Iceberg đã có snapshot sẵn, chỉ cần thêm 1 dòng log.
- **Feedback loop — nay đã có bản MVP.** `/interact` + `source_db_writer.py`
  ghi lại hành động "mua" của người dùng, đi qua đúng cơ chế watermark để trở
  thành dữ liệu huấn luyện ở chu kỳ sau. Phần **còn thiếu** (ghi nhận đúng ở
  báo cáo kỹ thuật mục 11): log đầy đủ request/response (impression, click
  không dẫn tới mua) để tính online metric thật — vẫn là hướng phát triển,
  không bắt buộc cho MVP.

**Có thể bỏ qua ở giai đoạn MVP (không sai nếu thiếu, nhưng nên biết là thiếu):**
- **Alertmanager** (cảnh báo khi latency/error rate vượt ngưỡng) — Prometheus
  + Grafana đủ để *xem*, nhưng không tự động *báo*. Với quy mô demo/bảo vệ,
  không cần thiết; chỉ nên nhắc ở "hướng phát triển".
- **Secret management riêng** (Vault/SOPS) — `.env.example` + biến môi trường
  Docker Compose là đủ cho một khóa luận chạy local, không cần thêm công cụ.
- **A/B testing / shadow deployment cho model** — đã có `promote.py` (gate
  đơn giản dựa trên offline metric) là đủ hợp lý cho phạm vi đề tài; A/B test
  online là một đề tài khác, không nên cố nhét vào.

**Kết luận:** với các phần đã bổ sung (source-db, MLflow infra, Evidently
job, validators, requirements tách theo image, docs, model CI/CD, đóng gói
`libs/` thành package có version, và nay thêm lớp tín hiệu tương tác tức thời
`/interact` + `ui/` + `tests/load/`), project đã đủ đại diện cho một hệ
MLOps hoàn chỉnh ở quy mô MVP, có nền tảng tổ chức tốt để mở rộng theo cả
chiều ngang (thêm job/feature mới) lẫn chiều dọc (thay đổi cách các phần phụ
thuộc lẫn nhau mà không gây lỗi âm thầm), và có một demo trực quan, thuyết
phục cho buổi bảo vệ. Điểm còn lại đáng cân nhắc: một thư mục `docs/
decisions/` kiểu ADR để ghi lại các quyết định kiến trúc đã có (Spark→DuckDB,
không dùng dbt, thêm Prometheus/Grafana, thêm `/interact`…) — hữu ích khi cần
giải trình lý do lựa chọn lúc bảo vệ khóa luận.

## Version hoá theo Git SHA

Mỗi image build ra gắn tag `git rev-parse --short HEAD`, ví dụ
`recommendation-transform:a1b2c3d`. `docker-compose.yml` và (sau này) Helm
values đều tham chiếu cùng tag này — đảm bảo Compose và Kubernetes luôn chạy
đúng cùng một phiên bản code.
