# BÁO CÁO KỸ THUẬT
## Kiến trúc Pipeline MLOps cho Hệ thống Gợi ý Sản phẩm — Giai đoạn MVP

---

## 1. Mục tiêu và phạm vi

### 1.1 Mục tiêu
Tài liệu này mô tả chi tiết kiến trúc kỹ thuật và các quyết định thiết kế của
pipeline MLOps phục vụ hệ thống gợi ý sản phẩm (recommendation system), được
xây dựng trong khuôn khổ khóa luận tốt nghiệp. Mục tiêu của kiến trúc là mô
phỏng đầy đủ vòng đời MLOps thực tế (ingest → lưu trữ → biến đổi → feature
store → huấn luyện → registry → phục vụ → giám sát) ở quy mô có thể triển
khai và vận hành hoàn toàn trên máy cục bộ (local), trong thời gian và nguồn
lực của một sinh viên thực hiện độc lập.

### 1.2 Phạm vi
- **Nằm trong phạm vi**: pipeline dữ liệu batch/incremental, lakehouse dựa
  trên Apache Iceberg, feature store, huấn luyện mô hình ranking, model
  registry, API phục vụ gợi ý, giám sát vận hành và giám sát chất lượng dữ
  liệu/mô hình, cùng một lớp tín hiệu tương tác tức thời ở tầng serving (mục
  8.1) làm bản MVP của feedback loop.
- **Nằm ngoài phạm vi chính** (được nêu rõ trong đề cương và giữ nguyên ở
  bản kiến trúc này): triển khai quy mô cloud lớn, tối ưu hạ tầng Kubernetes
  cho production, xử lý luồng thời gian thực (streaming/CDC) ở tầng dữ liệu
  cốt lõi. Lớp tín hiệu tức thời ở mục 8.1 không phải streaming/CDC — đây là
  cơ chế đọc/ghi trực tiếp ở tầng serving, tách biệt hoàn toàn khỏi pipeline
  batch chính (giải thích rõ ở mục 8.1).
- Các nội dung ngoài phạm vi chính vẫn được **thiết kế để không chặn đường
  mở rộng** sau này (xem mục 11 — Hướng phát triển), nhưng không được cài đặt
  chi tiết trong giai đoạn MVP.

---

## 2. Tổng quan kiến trúc

Pipeline được tổ chức thành 7 tầng chức năng độc lập, giao tiếp với nhau
thông qua **Apache Iceberg (REST Catalog)** làm lớp lưu trữ trung tâm — mọi
tầng đọc/ghi qua Iceberg thay vì gọi trực tiếp vào nhau, giúp mỗi tầng có thể
thay đổi công nghệ nội bộ (ví dụ đổi engine xử lý) mà không ảnh hưởng các
tầng còn lại.

```
┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌───────────────┐
│  Source DB   │──▶│   Extract    │──▶│  Bronze/    │──▶│   Feature     │
│ (giả lập)    │◀──│  (dlt/watermark)│  Silver/Gold │   │   Store       │
└──────▲──────┘   └──────────────┘   │  (Iceberg)   │   │ (Feast/Redis) │
       │                              └─────────────┘   └───────┬───────┘
       │ ghi trực tiếp (B)                                       │
       │                    ┌──────────────┐   ┌─────────────┐  │
       │                    │   Serving    │◀──│   Training  │◀─┘
       │                    │  (FastAPI)   │   │ (LightGBM + │
       │                    └──┬───────┬───┘   │  MLflow)    │
       │           UI (Streamlit)│      │       └─────────────┘
       └───────────────────────┘      ┌▼───────────┐   ┌─────────────┐
              nút "Đã mua"             │  Monitoring│   │  Monitoring │
              → POST /interact         │ (Prometheus/│   │ (Evidently  │
              → (A) Redis, (B) source-db│  Grafana)  │   │    AI)      │
                                        └────────────┘   └─────────────┘
```

Toàn bộ orchestration (điều phối thứ tự chạy, lịch chạy, retry) do **Apache
Airflow** đảm nhiệm, nhưng Airflow chỉ gọi các job đã được đóng gói sẵn trong
image Docker riêng — không chứa business logic. Nguyên tắc này được giữ xuyên
suốt toàn bộ thiết kế (xem mục 10.1). Nhánh `/interact` ở mục 8.1 là ngoại lệ
duy nhất đi trực tiếp vào `source-db` mà không qua Airflow — vì đây là ghi
đơn lẻ theo request, không phải job batch, nên không phù hợp mô hình
DockerOperator; dữ liệu ghi vào vẫn đi qua đúng con đường watermark ở lần
`dag_ingest` chạy kế tiếp như dữ liệu nguồn thật.

---

## 3. Lựa chọn công nghệ và lý do

| Thành phần | Công nghệ | Lý do chọn |
|---|---|---|
| Compute engine xử lý batch | **DuckDB + Polars** (thay vì Spark) | Quy mô dữ liệu MVP (một domain Amazon review) không cần cụm phân tán; DuckDB chạy embedded, không cần JVM/cluster, giảm đáng kể thời gian dev và tài nguyên máy cục bộ. |
| Lakehouse table format | **Apache Iceberg**, REST Catalog | Tách rời compute engine khỏi storage — cho phép sau này chuyển sang Spark mà không phải viết lại tầng lưu trữ. REST Catalog dùng chung được cho nhiều engine (DuckDB, Spark, Trino…). |
| Commit vào Iceberg | **PyIceberg** (không dùng DuckDB Iceberg writer) | Khả năng ghi Iceberg native của DuckDB tại thời điểm thiết kế còn chưa ổn định; PyIceberg là đường ghi trưởng thành hơn cho các thao tác schema evolution, snapshot, partition. |
| Object storage | **MinIO** | Tương thích S3 API, chạy local dễ dàng, là backend lưu trữ vật lý cho Iceberg và artifact của MLflow. |
| Extract / EL | **dlt** | Hỗ trợ sẵn incremental cursor, kết nối nguồn dữ liệu quan hệ, giảm code tự viết cho phần trích xuất. |
| Feature store | **Feast + Redis** | Feast là chuẩn phổ biến cho feature store mã nguồn mở; Redis phục vụ online serving với độ trễ thấp. **Lưu ý kỹ thuật**: Feast chưa đọc trực tiếp bảng Iceberg làm offline source ổn định, nên có bước export Iceberg → Parquet snapshot trước khi Feast materialize. |
| Huấn luyện mô hình | **LightGBM (ranker)** | Phù hợp bài toán ranking/gợi ý, chi phí huấn luyện thấp, dễ diễn giải feature importance cho báo cáo. |
| Model registry / tracking | **MLflow** | Theo dõi experiment, lưu artifact model, quản lý version và promotion gate. |
| Serving | **FastAPI** | Nhẹ, hiệu năng tốt, dễ tích hợp middleware đo lường (Prometheus instrumentator). |
| Orchestration | **Airflow (DockerOperator)** | Mỗi job chạy trong container riêng, cô lập dependency giữa các job (ví dụ image training không cần cài fastapi). |
| Giám sát vận hành | **Prometheus + Grafana** | Đo trực tiếp các tiêu chí đánh giá đã đặt ra trong đề cương (latency p95, tỷ lệ cache hit); chi phí tích hợp thấp nhờ `prometheus-fastapi-instrumentator`. |
| Giám sát dữ liệu/mô hình | **Evidently AI** | Phát hiện data drift, model quality drift — bổ sung cho Prometheus/Grafana (vốn chỉ đo metric vận hành, không đo chất lượng dữ liệu). |

### 3.1 Công nghệ cân nhắc nhưng không đưa vào MVP

**dbt** được cân nhắc nhưng quyết định không sử dụng ở giai đoạn này, vì hai
lý do: (1) adapter `dbt-duckdb` tại thời điểm thiết kế vẫn chưa hỗ trợ chính
thức Iceberg làm định dạng đích — hỗ trợ Iceberg vẫn nằm trong lộ trình phát
triển của adapter, chưa phải tính năng ổn định; (2) phần lớn lợi ích của dbt
(SQL có tổ chức, schema contract, lineage) đã được đáp ứng bằng cấu trúc
`sql/` + `libs/iceberg/schema_contracts/` tự thiết kế, nên thêm dbt vào lúc
này tăng rủi ro tích hợp mà không mang lại giá trị tương xứng cho quy mô đề
tài. Ghi nhận là hướng mở rộng khi hệ thống lớn hơn (mục 11).

**Kubernetes/CI-CD đầy đủ** cũng được cân nhắc nhưng giữ ở mức placeholder
(`infra/k8s/`) thay vì cài đặt chi tiết, để nhất quán với phạm vi nghiên cứu
đã khai báo (không tối ưu hạ tầng Kubernetes production).

### 3.2 Công cụ hỗ trợ trình bày và kiểm thử tải

Hai công cụ dưới đây **không thuộc pipeline MLOps cốt lõi** — chúng phục vụ
việc trình bày/kiểm thử, được tách bạch rõ để không gây hiểu nhầm là thành
phần production:

| Công cụ | Vai trò |
|---|---|
| **Streamlit** (`ui/`) | Giao diện demo tối giản — hiển thị danh sách gợi ý, cho phép bấm "Đã mua" để kích hoạt luồng ở mục 8.1. Không phải giao diện người dùng cuối của một hệ thống thương mại điện tử thật, chỉ nhằm mục đích trình bày trực quan thay vì gọi API bằng `curl`/Postman. |
| **Locust** (`tests/load/locustfile.py`) | Sinh traffic giả lập gọi `/recommend/homepage` và `/recommend/similar`, dùng để (a) có số liệu latency/throughput thật hiển thị trên dashboard Grafana thay vì dashboard trống, và (b) kiểm tra sơ bộ hành vi API dưới tải nhẹ. Không phải công cụ benchmark quy mô production. |

---

### 3.3 Công nghệ cho hướng mở rộng (ngoài MVP1)

**PyTorch** (CPU build) — dùng riêng cho `train_sequence.py` (mục 7.6), tách
biệt hoàn toàn khỏi `requirements/training.txt` phần MVP1 nếu cần, để không
làm nặng image training khi chưa cần tới sequence model.

---

## 4. Chiến lược ingestion và incremental loading

### 4.1 Vấn đề
Bộ dữ liệu gốc (Amazon review/metadata) là dữ liệu tĩnh dạng file, không tự
phát sinh thay đổi theo thời gian. Để pipeline phản ánh đúng bài toán thực tế
(dữ liệu mới phát sinh liên tục), một **cơ sở dữ liệu nguồn giả lập**
(`infra/source-db/`, Postgres) được dựng riêng, đóng vai trò hệ thống OLTP
bên ngoài mà job extract sẽ kết nối tới — tách biệt hoàn toàn khỏi lakehouse.

### 4.2 Cơ chế watermark
Thay vì lấy dữ liệu theo cửa sổ thời gian cố định (ví dụ "24 giờ gần nhất" —
dễ bỏ sót dữ liệu đến trễ), job extract sử dụng hai mốc:

- `last_watermark`: giá trị `updated_at` lớn nhất đã xử lý thành công ở lần
  chạy trước, được lưu bền vững (không lưu trong container).
- `current_watermark`: giá trị `updated_at` lớn nhất của tập dữ liệu vừa lấy
  được ở lần chạy hiện tại.

Câu truy vấn dạng `WHERE updated_at > :last_watermark` đảm bảo chỉ lấy đúng
phần dữ liệu mới, không lấy trùng, không bỏ sót dữ liệu chỉnh sửa muộn. Sau
khi job Bronze commit thành công, `current_watermark` mới được ghi đè thành
`last_watermark` mới — đảm bảo tính idempotent khi job chạy lại do lỗi.

### 4.3 Kịch bản kiểm chứng
`scripts/simulate_incremental_update.py` định kỳ insert/update một lô bản
ghi mới vào source-db với `updated_at` hiện tại, cho phép chạy DAG extract
nhiều lần với dữ liệu nguồn thay đổi giữa các lần — chứng minh cơ chế
watermark hoạt động đúng trên dữ liệu thật thay vì chỉ mô tả lý thuyết. Từ
mục 8.1, `POST /interact` là một nguồn ghi bổ sung vào cùng bảng
`interactions` với `updated_at = now()` — đi qua đúng cơ chế watermark này ở
lần `dag_ingest` kế tiếp, không cần thêm đường xử lý riêng.

---

## 5. Lakehouse: Bronze — Silver — Gold

| Tầng | Nội dung | Engine ghi | Đặc điểm |
|---|---|---|---|
| Bronze | Dữ liệu thô, gần như nguyên bản từ source-db | DuckDB (transform) qua PyIceberg (commit) | Append-only, giữ nguyên lịch sử |
| Silver | Dữ liệu đã làm sạch, chuẩn hóa kiểu dữ liệu, khử trùng lặp | DuckDB (transform) qua PyIceberg (commit) | Có schema contract enforce bằng validator (mục 5.1) |
| Gold | Bảng đặc trưng (feature) sẵn sàng cho feature store và training | DuckDB/Polars (transform) qua PyIceberg (commit) | Aggregation theo user/item, tính theo batch định kỳ |

### 5.1 Schema contract và validate
Mỗi bảng có một khai báo schema tường minh trong
`libs/iceberg/schema_contracts/` — dùng chung cho cả engine DuckDB hiện tại
lẫn engine Spark nếu chuyển đổi sau này, tránh lệch schema giữa hai đường xử
lý. Trước khi commit vào Iceberg, dữ liệu được validate qua
`libs/iceberg/validators.py` (dùng pandera) — đảm bảo một job lỗi không âm
thầm ghi sai schema vào bảng downstream mà không ai phát hiện cho tới khi job
sau đó fail.

### 5.2 Bảo trì
Job `jobs/maintenance/compact_iceberg.py` chạy định kỳ (qua
`dag_maintenance.py`) để compact các file nhỏ phát sinh do ghi incremental
nhiều lần, và expire snapshot cũ để kiểm soát dung lượng MinIO.

---

## 6. Feature Store

Feast được dùng làm feature store, với `feature_repo/` là một repo độc lập ở
gốc project (theo đúng chuẩn cấu trúc Feast, không lồng trong `jobs/`).

- **Offline store**: do Feast chưa hỗ trợ ổn định đọc trực tiếp bảng Iceberg,
  bảng Gold được export sang Parquet snapshot (`jobs/materialize/
  export_to_parquet.py`) trước khi Feast đọc làm offline source.
- **Online store**: Redis, phục vụ tra cứu đặc trưng độ trễ thấp tại thời
  điểm serving. Redis cũng là nơi lưu tín hiệu tương tác tức thời (mục 8.1)
  và kết quả `similar_items` được vật chất hoá sẵn — dùng chung Redis instance
  nhưng phân biệt bằng tiền tố key, không lẫn với online feature của Feast.
- **Materialize**: `jobs/materialize/feast_materialize.py` đẩy giá trị mới
  nhất từ offline store sang Redis theo lịch (`dag_materialize.py`).

---

## 7. Huấn luyện và Model Registry

### 7.1 Quy trình huấn luyện
1. `build_training_set.py` — tạo tập huấn luyện từ Gold layer, temporal split
   (tách theo thời gian, tránh leakage), group theo user/query cho bài toán
   ranking.
2. `negative_sampling.py` — sinh negative sample cho bài toán ranking.
3. `train_ranker.py` — huấn luyện LightGBMRanker, log toàn bộ tham số,
   metric, artifact model vào MLflow.
4. `evaluate.py` — tính NDCG, Recall, MAP, Coverage trên tập validation.
5. `promote.py` — cổng promotion: chỉ đăng ký model mới vào registry ở trạng
   thái "production" nếu metric vượt ngưỡng so với model hiện tại.

### 7.2 Hạ tầng MLflow
MLflow tracking server được tách thành service riêng (`infra/mlflow/`), có
backend store (Postgres) và artifact root trỏ về MinIO. Backend store này
**tách biệt** khỏi Postgres của source-db, để không lẫn giữa "dữ liệu nghiệp
vụ giả lập" và "metadata thật của hệ thống MLOps".

### 7.3 CI/CD cho model

CI/CD trong project được tách thành hai luồng độc lập, dễ nhầm lẫn nếu gộp
chung:

- **CI/CD cho code/hạ tầng**: `ci.yml` (lint, test) và `build-push-images.yml`
  (build/push Docker image) — kiểm tra code đúng và image build được, không
  liên quan đến chất lượng model.
- **CI cho model**: job `model-smoke-test` trong `ci.yml` chạy
  `train_ranker.py` + `evaluate.py` trên một tập dữ liệu mẫu rất nhỏ (`tests/
  model/sample_data/`, cố định seed, commit sẵn trong repo). Mục đích duy
  nhất là xác nhận pipeline training không lỗi cú pháp/logic khi có thay đổi
  code — **không** dùng để đánh giá chất lượng model thật, vì tập mẫu quá nhỏ
  để có ý nghĩa thống kê. Việc đánh giá thật vẫn do `dag_training.py` chạy
  trên dữ liệu đầy đủ theo lịch Airflow.
- **CD cho model**: trước khi bổ sung, `promote.py` đánh dấu model mới là
  "production" trong MLflow registry nhưng `model_loader.py` chỉ load model
  một lần lúc container khởi động — model mới không được dùng cho tới khi
  restart thủ công. Khắc phục bằng cách cho `model_loader.py` poll registry
  định kỳ (qua `libs/mlflow_utils/registry_client.py`, dùng chung logic truy
  vấn với `promote.py`); khi phát hiện version "production" thay đổi, serving
  tự động chuyển sang model mới mà không cần redeploy — đóng vòng lặp
  train → promote → serve.

### 7.4 Data-to-model lineage
`train_ranker.py` log thêm `iceberg_snapshot_id` của dữ liệu dùng để tạo tập
training vào MLflow (qua `libs/mlflow_utils/logging_helpers.py`). Vì Iceberg
đã có snapshot tự nhiên, việc này gần như không tốn thêm chi phí kỹ thuật,
nhưng cho phép mỗi model version truy vết chính xác đã train trên dữ liệu
nào — yêu cầu cơ bản của reproducibility trong MLOps.

### 7.5 Model interface — chuẩn bị cho việc thêm model khác kiểu

Hiện tại chỉ có một loại model (LightGBM ranker), nhưng `serving/app/core/
model_loader.py` không gọi trực tiếp API cụ thể của LightGBM (kiểu
`model.predict(dataframe)`). Thay vào đó, mọi model implement một interface
mỏng dùng chung, khai báo tại `libs/mlflow_utils/../ranking/base.py`:

```
predict(user_id, candidate_items) -> scores
```

Chi tiết bên trong (model cần input là bảng feature phẳng hay chuỗi hành vi)
nằm gọn trong từng implementation, không lộ ra tầng API
(`homepage.py`/`similar.py`). Quyết định này được đưa vào ngay từ khi cài
đặt `train_ranker.py`/`model_loader.py` (không phải thêm sau), vì chi phí
làm ngay rất nhỏ (một interface mỏng) trong khi chi phí sửa lại sau — nếu đã
có nhiều điểm gọi model rải rác giả định sẵn kiểu input cụ thể — lớn hơn
đáng kể. Đây là điều kiện tiên quyết để mục 7.6 có thể triển khai mà không
phải sửa lại tầng serving.

### 7.6 Sequence model — hướng mở rộng (ngoài MVP1)

Model ranking hiện tại (LightGBM) coi mỗi feature độc lập tại một thời điểm,
**không mô hình hoá được thứ tự hành vi gần đây** của người dùng — đây là
một giới hạn đã ghi nhận khi so sánh với các kiến trúc gợi ý dùng sequence
model (xem thêm mục 11). Hướng mở rộng được chọn để giải quyết giới hạn này,
**tách hẳn khỏi phạm vi MVP1**, là một bản rút gọn của **GRU4Rec**:

- **Kiến trúc**: Embedding layer (dim nhỏ, ví dụ 32) → GRU một lớp (hidden
  size nhỏ, ví dụ 64) → Linear + **sampled softmax** (không tính softmax đầy
  đủ trên toàn vocab — đây là phần tốn chi phí tính toán nhất nếu bỏ qua kỹ
  thuật negative sampling, vốn cũng chính là kỹ thuật đã dùng trong
  `negative_sampling.py` của LightGBM).
- **Vì sao chọn GRU4Rec rút gọn thay vì self-attention (SASRec) hay BERT4Rec**:
  độ phức tạp tính toán của attention tăng theo bình phương độ dài chuỗi,
  trong khi RNN với chuỗi ngắn (10–20 item) rẻ hơn đáng kể và hội tụ nhanh
  hơn ở quy mô dữ liệu MVP; BERT4Rec cần chiến lược masked-item training
  phức tạp hơn, không cần thiết cho mục tiêu so sánh ở đây.
- **Thư viện**: PyTorch thuần, tự viết training loop — tránh các framework
  đóng hộp (Transformers4Rec, PyTorch-Lightning) để giữ nhẹ và dễ giải trình
  từng bước khi bảo vệ.
- **Tích hợp**: dùng lại đúng nguồn dữ liệu (Gold layer qua Iceberg), đúng
  MLflow tracking/registry, đúng bộ metric đánh giá (NDCG/Recall/MAP) như
  LightGBM — chỉ khác ở nguồn dataset (`build_sequence_dataset.py` sinh chuỗi
  thay vì bảng phẳng) và bản thân model. Nhờ interface ở mục 7.5, việc thêm
  model này **không đòi hỏi sửa lại** `homepage.py`/`similar.py`.
- **Vị trí trong lộ trình**: đây là phase độc lập, chỉ triển khai sau khi
  MVP1 hoàn chỉnh (xem `de-xuat-trien-khai.md`, Phase 9) — không nằm trên
  đường găng, có thể bỏ qua hoàn toàn nếu không còn thời gian mà không ảnh
  hưởng tính đầy đủ của MVP1.
- **Kết quả có thể là "chưa vượt trội hơn LightGBM"**: ở quy mô dữ liệu MVP,
  sequence model có thể không cho kết quả tốt hơn model feature-based — đây
  vẫn là một so sánh có giá trị khoa học nếu quy trình đánh giá (cùng tập
  test, cùng metric) được thực hiện đúng, không phải một kết quả thất bại.

---

## 8. Serving

FastAPI phục vụ hai endpoint gợi ý chính: gợi ý trang chủ
(`/recommend/homepage`) và sản phẩm tương tự (`/recommend/similar`). Luồng
xử lý một request:

1. Kiểm tra cache Redis trước (`core/cache.py`) — giảm độ trễ cho các
   truy vấn lặp lại.
2. Nếu cache miss: lấy model đang ở trạng thái "production" đã được
   `model_loader.py` cache sẵn trong bộ nhớ (được refresh định kỳ bằng cách
   poll MLflow registry — xem mục 7.3, không load lại từ MLflow mỗi request),
   tra cứu đặc trưng từ Redis (Feast online store), chạy inference.
3. Kết quả sau cùng được đi qua bước re-rank nhẹ dựa trên tín hiệu tương tác
   tức thời, nếu có (mục 8.1), trước khi trả về.
4. Nếu model hoặc feature không sẵn sàng (lỗi hạ tầng, cold-start user/item
   mới): fallback sang danh sách sản phẩm phổ biến (`core/fallback.py`) —
   đảm bảo API luôn trả về kết quả hợp lệ thay vì lỗi 5xx.
5. Mọi request được đo latency, request count qua middleware Prometheus
   (`core/metrics.py`).

### 8.1 Tín hiệu tương tác tức thời (`/interact`)

**Vấn đề cần giải quyết**: model ranking được huấn luyện theo chu kỳ batch
(mục 7), nên không phản ánh được hành vi vừa xảy ra trong phiên hiện tại của
người dùng (ví dụ vừa mua một sản phẩm). Đây cũng là mảnh còn thiếu đã nêu ở
mục 11 của bản thiết kế trước — "chưa có vòng lặp phản hồi (feedback loop)".
Mục này hiện thực hoá một bản MVP của feedback loop đó, giới hạn phạm vi rõ
ràng để không lấn sang streaming/CDC (đã loại khỏi phạm vi ở mục 1.2).

**Thiết kế**: một endpoint mới `POST /interact` nhận `{user_id, item_id,
event_type}`, thực hiện **hai việc độc lập, không chặn nhau**:

- **(A) Ghi Redis ngay** — đẩy `item_id` vào danh sách `recent_items:{user_id}`
  (giới hạn kích thước, có TTL vài chục phút vì đây là tín hiệu phiên, không
  phải lưu trữ dài hạn). Đây là tín hiệu duy nhất được dùng để re-rank ngay
  lập tức.
- **(B) Ghi vào `source-db`** — một dòng mới trong bảng `interactions` với
  `updated_at = now()`, đi qua đúng cơ chế watermark đã có (mục 4.2) ở lần
  `dag_ingest` kế tiếp — đảm bảo sự kiện này thật sự trở thành một phần dữ
  liệu huấn luyện ở chu kỳ retrain sau, không chỉ là hiệu ứng hiển thị tạm
  thời trên giao diện.

**Re-rank tại thời điểm phục vụ** (`serving/app/core/recency_boost.py`): sau
khi có danh sách candidate + score từ model như bình thường, đọc
`recent_items:{user_id}` từ Redis; với mỗi item vừa tương tác, tra thêm danh
sách sản phẩm tương tự (kết quả của `jobs/candidates/similar_items.py`, đã
được vật chất hoá sẵn vào Redis) và ưu tiên các item này lên đầu danh sách trả
về.

**Ranh giới cần nói rõ khi trình bày**: đây **không phải** retrain thời gian
thực và **không phải** streaming/CDC. Việc học từ sự kiện này vẫn đi qua
đúng chu kỳ batch (extract → Bronze → Silver → Gold → training) như thiết kế
gốc; chỉ có bước re-rank ở tầng serving là tức thời, dựa trên tín hiệu phiên
lưu tạm ở Redis. Nhánh (A) phục vụ trải nghiệm/demo, nhánh (B) đảm bảo tính
đúng đắn lâu dài của hệ thống.

### 8.2 Giao diện demo (UI)

`ui/` là một ứng dụng Streamlit độc lập, gọi trực tiếp vào FastAPI qua HTTP —
không nằm trong luồng nghiệp vụ chính, chỉ phục vụ trình bày trực quan.
Hiển thị danh sách gợi ý và nút "Đã mua" cho mỗi sản phẩm; bấm nút gọi
`POST /interact` rồi gọi lại `/recommend/homepage`, hiển thị danh sách gợi ý
mới ngay trong cùng một lượt render.

---

## 9. Giám sát (Monitoring)

Hệ thống có hai lớp giám sát tách biệt, đo hai loại chỉ số khác nhau:

### 9.1 Giám sát vận hành — Prometheus + Grafana
Đo các chỉ số đã được đặt ra trong tiêu chí đánh giá của đề cương: latency
p95 của API, tỷ lệ cache hit, request rate, error rate. Prometheus scrape
`/metrics` từ service serving; Grafana đọc dashboard được provision sẵn dạng
JSON (`infra/monitoring/grafana/dashboards/`), không cần dựng dashboard thủ
công khi demo/bảo vệ. Trước buổi trình bày, `tests/load/locustfile.py` (mục
3.2) có thể chạy trong vài phút để dashboard có traffic thật thay vì trống.

### 9.2 Giám sát dữ liệu và mô hình — Evidently AI
`jobs/monitoring/data_drift_report.py` so sánh phân phối dữ liệu Silver hôm
nay với baseline; `model_quality_report.py` theo dõi drift của prediction và
feature quan trọng. Report (HTML/JSON) được ghi lên MinIO qua
`report_sink.py`, chạy định kỳ qua `dag_monitoring.py`.

Hai lớp giám sát này **không chồng lấn**: Prometheus/Grafana trả lời câu hỏi
"hệ thống có đang chạy tốt không (về mặt hạ tầng)", còn Evidently trả lời câu
hỏi "dữ liệu/mô hình có đang lệch khỏi kỳ vọng không (về mặt thống kê)".

---

## 10. Nguyên tắc kiến trúc và tổ chức mã nguồn

### 10.1 Nguyên tắc cốt lõi
1. Airflow **chỉ** orchestration — DAG gọi `python -m jobs.xxx.run`, không
   chứa business logic, cho phép test từng job độc lập ngoài Airflow.
2. Mỗi job/nhóm job được đóng thành một Docker image riêng (extract,
   transform, training, serving, monitoring, airflow) — chạy qua
   DockerOperator, cô lập dependency giữa các job.
3. Code và SQL được **đóng vào image** khi build (`COPY`), không bind mount
   ở môi trường production-like, đảm bảo tính tái lập giữa các lần chạy.
4. Iceberg là **source of truth duy nhất** giữa các tầng batch — không tầng
   nào gọi trực tiếp API nội bộ của tầng khác. `POST /interact` (mục 8.1) là
   ngoại lệ có chủ đích, đã giải thích rõ ranh giới ở mục 2 và 8.1.
5. Mỗi job có input/output table tường minh, được enforce bằng schema
   contract + validator, không suy luận ngầm định.
6. Không lưu trạng thái quan trọng trên filesystem cục bộ của container —
   mọi dữ liệu đi qua MinIO/Iceberg/MLflow artifact store/Redis.
7. Mỗi image được gắn version theo Git SHA (`git rev-parse --short HEAD`),
   đảm bảo Docker Compose và (khi mở rộng) Kubernetes luôn chạy đúng một
   phiên bản code xác định.

### 10.2 Cấu trúc thư mục
Chi tiết đầy đủ cấu trúc thư mục source code được trình bày trong tài liệu
riêng `cau-truc-project.md`, tổ chức theo các nhóm chính: `jobs/` (business
logic), `sql/` (SQL thuần tách khỏi orchestration), `libs/` (code dùng
chung, schema contract), `airflow/` (DAG + custom operator), `serving/`
(FastAPI, gồm cả `/interact` và re-rank tức thời — mục 8.1), `ui/` (giao
diện demo Streamlit — mục 8.2), `feature_repo/` (Feast), `infra/` (hạ tầng
Docker/monitoring/mlflow/source-db), `requirements/` (dependency tách theo
image), `tests/load/` (Locust — mục 3.2), và `docs/` (tài liệu kỹ thuật,
data dictionary, model card).

---

## 11. Hạn chế và hướng phát triển

| Hạn chế hiện tại | Hướng phát triển |
|---|---|
| Feedback loop mới ở mức MVP — `/interact` (mục 8.1) mới ghi lại hành động "mua", chưa ghi lại log request/response đầy đủ của serving (impression, click không dẫn tới mua) để tính online metric thật | Thêm job đọc log serving, nạp vào bảng Bronze riêng (`bronze_serving_requests`); đã có sẵn placeholder trong thiết kế thư mục |
| Chưa có kiểm tra lệch train/serve (feature offline tính trong Gold layer khác đường tính với feature online tra cứu từ Redis) | Thêm `tests/data_quality/test_train_serve_skew.py` so sánh giá trị feature giữa hai đường tại cùng thời điểm |
| Compute engine DuckDB giới hạn khả năng mở rộng khi dữ liệu lớn hơn nhiều so với MVP | Do đã tách Iceberg làm source of truth độc lập với engine, việc chuyển sang Spark chỉ cần thay job transform, không ảnh hưởng các tầng khác |
| Chưa có cảnh báo tự động (alerting) khi metric vận hành vượt ngưỡng | Bổ sung Alertmanager trên nền Prometheus đã có sẵn |
| Chưa dùng dbt cho quản lý transform SQL | Cân nhắc khi adapter dbt-duckdb hỗ trợ Iceberg ổn định, hoặc khi chuyển hẳn sang dbt-spark |
| Chưa có A/B testing / shadow deployment cho model | Nằm ngoài phạm vi đề tài hiện tại, ghi nhận là hướng mở rộng riêng |
| Chưa đóng vòng lặp drift → retraining tự động (Evidently phát hiện drift nhưng không tự kích hoạt `dag_training`) | Có thể nối bằng Airflow Variable: report vượt ngưỡng → set flag → `dag_training` kiểm tra flag trước khi chạy sớm hơn lịch |
| Chưa có quy trình rollback model rõ ràng nếu version mới gây lỗi ở serving | MLflow registry đã giữ lịch sử version; cần ghi quy trình rollback thủ công vào `docs/runbook.md` |
| Chưa tối ưu triển khai Kubernetes/production-scale | Đã có placeholder `infra/k8s/helm/`, không nằm trong phạm vi chính của đề tài |
| Ranking model hiện tại (LightGBM) không mô hình hoá thứ tự hành vi gần đây của người dùng | Đã có hướng mở rộng cụ thể: mini-GRU4Rec (mục 7.6), triển khai ở Phase 9 sau khi MVP1 hoàn chỉnh, dùng chung interface model đã chuẩn bị sẵn (mục 7.5) |
| Re-rank tức thời (mục 8.1) mới dựa trên `similar_items` đơn giản, chưa tính đến diversity/business rule | Có thể mở rộng `recency_boost.py` để trộn nhiều nguồn candidate (similar_items, popular_items) có trọng số, thay vì chỉ đẩy lên đầu |

---

## 12. Kết luận

Kiến trúc được thiết kế theo nguyên tắc "đủ nhẹ để chạy local, đủ đúng
nguyên lý để mở rộng" — mọi quyết định đơn giản hóa (DuckDB thay Spark, không
dùng dbt, không triển khai CDC/Kubernetes) đều được thực hiện có chủ đích và
đi kèm đường mở rộng rõ ràng, không phải cắt giảm tùy tiện. Việc tách rời
compute engine khỏi lưu trữ (qua Iceberg + REST Catalog) là quyết định kiến
trúc trung tâm, giúp toàn bộ hệ thống có thể nâng cấp từng phần (engine xử
lý, orchestration, hạ tầng triển khai) mà không phải viết lại từ đầu. Lớp
tín hiệu tương tác tức thời ở mục 8.1 được bổ sung theo đúng nguyên tắc đó —
mở rộng có kiểm soát, ranh giới rõ ràng với phần streaming/CDC đã chủ động
loại khỏi phạm vi, và tận dụng lại cơ chế watermark sẵn có thay vì xây dựng
một đường xử lý riêng.
