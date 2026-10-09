# Phase 3 — Feature Store (Feast + Redis)

> Trạng thái: **Hoàn thành**. Đối chiếu với `de-xuat-trien-khai.md` mục
> "Phase 3" — **đã điều chỉnh phạm vi** theo
> [ADR 0004](../decisions/0004-gru-sequence-model-thay-lightgbm.md) (GRU
> thay LightGBM làm model chính). Phụ thuộc: Phase 2.
>
> **Cập nhật 2026-09-27 (Phase 5)**: `feast[redis,aws]` hạ từ `0.66.0`
> xuống `0.55.0` trong `requirements/materialize.txt` — xung đột dependency
> thật với `mlflow-skinny` (cần thiết ở `serving`, image khác cũng dùng
> `feast`), xem [ADR 0007](../decisions/0007-downgrade-feast-for-mlflow-compat.md).
> Đã build lại `materialize-job` và kiểm chứng lại `feast apply`/
> `feast materialize-incremental`/`get_online_features()` vẫn hoạt động
> đúng với version mới — không có thay đổi hành vi nào khác.

Thư mục code: `jobs/features/`,
`jobs/materialize/`, `feature_repo/`.

---

## 1. Mục tiêu ban đầu

Có online feature lookup độ trễ thấp, sẵn sàng cho cả training và serving.
**Khác với đề xuất gốc** (viết cho LightGBM — rolling-window aggregate),
phạm vi phase này được thiết kế lại từ đầu cho GRU (sequence model, ADR
0004): feature chính không phải bảng đặc trưng phẳng mà là **chuỗi item_id
gần nhất theo thời gian của mỗi user**.

## 2. Những gì đã triển khai

### 2.1 `gold.user_features` — chuỗi hành vi theo user

- `libs/src/reco_mlops_libs/iceberg/schema_contracts/gold_user_features.py`
  — contract mới: `user_id`, `item_sequence` (list String, tối đa 10 phần
  tử), `event_time_sequence` (list Timestamptz song song), `sequence_length`,
  `last_event_time`, `computed_at`. `MAX_SEQUENCE_LENGTH = 10` — chốt trong
  buổi thảo luận kickoff Phase 3 (ngắn, huấn luyện nhanh trên CPU, đủ cho
  seed data hiện tại; có thể tăng khi chuyển sang Amazon Fashion dataset
  thật).
- `sql/gold/gold_user_features.sql` — dùng `ROW_NUMBER()` lấy 10 interaction
  gần nhất/user, rồi `array_agg(... ORDER BY event_time ASC)` để chuỗi theo
  đúng thứ tự thời gian tăng dần (cũ → mới) — quy ước quan trọng cho input
  GRU (dự đoán item tiếp theo).
- `jobs/features/build_user_features.py` — đọc toàn bộ `silver.interactions`
  qua PyIceberg, chạy SQL trên qua DuckDB, validate, `overwrite()` vào
  `gold.user_features`. Chạy trong image `transform-job` sẵn có (không tạo
  image mới) — cùng dependency (DuckDB/PyIceberg), đúng gợi ý
  `cau-truc-project.md` ("features/... build chung vào image transform").
- Chỉ user có ≥1 interaction mới xuất hiện trong bảng này — cold-start
  (user chưa từng tương tác) không có placeholder rỗng, xử lý ở tầng serving
  sau này (Phase 5), không phải ở Feature Store.
- **Bug phát hiện khi chạy thật**: `to_pandas()` chuyển cột list của Arrow
  thành `numpy.ndarray`, không phải `list` Python — check ban đầu dùng
  `isinstance(value, list)` luôn fail dù dữ liệu đúng. Sửa bằng duck typing
  (`hasattr(value, "__len__")`).
- `jobs/features/build_item_features.py`: **không viết** — quyết định trong
  buổi kickoff Phase 3, tái dùng thẳng `gold.item_features` (Phase 2). GRU
  học embedding trực tiếp từ `product_id`, không cần đặc trưng item dạng số
  để đưa vào model; bảng Phase 2 vẫn hữu ích cho candidate filtering sau
  này.

### 2.2 Export Parquet snapshot lên MinIO

- `jobs/materialize/export_to_parquet.py` — đọc `gold.item_features` +
  `gold.user_features` qua PyIceberg, ghi Parquet lên MinIO
  (`s3://feast-offline-store/gold_item_features.parquet`,
  `.../gold_user_features.parquet`) bằng `s3fs` + `pyarrow.parquet.write_table`.
  Full overwrite mỗi lần chạy, không tích luỹ snapshot cũ.
- Cast `price` (Decimal(12,2) trong Iceberg) sang `float64` trước khi ghi —
  Feast không có kiểu Decimal native trong `feast.types`.
- Bucket `feast-offline-store` thêm vào `infra/minio/init-buckets.sh`.

### 2.3 `feature_repo/` — Feast repo chuẩn

- `entities.py` — 2 entity: `user` (join key `user_id`), `product` (join
  key `product_id`), cả hai khai báo tường minh `value_type=ValueType.STRING`
  (API `Entity(value_type=...)` của Feast 0.66 nhận `feast.value_type.ValueType`,
  **không phải** `feast.types.String` — nhầm giữa hai module gây lỗi
  `TypeCheckError` lúc chạy thật, đã sửa).
- `data_sources.py` — 2 `FileSource` trỏ thẳng `s3://feast-offline-store/...`,
  `timestamp_field="computed_at"` (cả 2 bảng Gold đều overwrite toàn bộ mỗi
  lần chạy nên chỉ cần 1 mốc thời gian phẳng, không cần tách
  event-time/created-time).
- `features_user.py` — `FeatureView user_recent_items` (item_sequence,
  event_time_sequence, sequence_length, last_event_time), `ttl` rất dài vì
  đây là snapshot ghi đè, không phải chuỗi sự kiện có "hạn dùng".
- `features_item.py` — `FeatureView item_features`, map thẳng cột của
  `gold.item_features` (category, brand, price, num_*, avg_rating,
  distinct_users, last_interaction_at).
- `feature_store.yaml` — `provider: local`, `online_store: redis`
  (`redis:6379`, chỉ hoạt động trong mạng Docker `reco-net`), **`registry`
  đặt trên MinIO** (`s3://feast-offline-store/registry.db`) — xem mục 2.4.

### 2.4 Bug quan trọng phát hiện khi chạy thật: registry cục bộ mất state giữa các lần `docker compose run`

`feast apply` ghi registry vào file cục bộ (`data/registry.db` theo cấu
hình Feast "local provider" mặc định). Vì mỗi lệnh `docker compose run` tạo
container **mới hoàn toàn**, registry ghi trong lần `feast apply` không còn
tồn tại ở lần gọi `get_online_features()` sau đó (container khác) —
`FeatureViewNotFoundException`. Đây đúng là lớp lỗi đã gặp và sửa ở
[ADR 0003](../decisions/0003-extract-writes-directly-to-minio.md) (Extract),
lặp lại ở một chỗ khác của hệ thống.

**Sửa**: đổi `registry` sang `s3://feast-offline-store/registry.db` (Feast
hỗ trợ registry path là URI S3 trực tiếp qua `feast[aws]`). Đã kiểm chứng:
gọi `get_online_features()` từ một container **hoàn toàn mới** (không chia
sẻ gì với container đã `feast apply`/`materialize`) vẫn trả đúng kết quả.

### 2.5 `jobs/materialize/feast_materialize.py`

Gọi thẳng CLI `feast apply` rồi `feast materialize-incremental <now>` qua
`subprocess` (không tự viết lại logic parse repo bằng Feast SDK) — vừa đơn
giản, vừa khớp nguyên văn cách diễn đạt Definition of Done trong
`de-xuat-trien-khai.md`.

### 2.6 Hạ tầng

- **Image `materialize` riêng** (`infra/docker/materialize/Dockerfile`,
  `requirements/materialize.txt` = `feast[redis,aws]==0.66.0`) — quyết định
  trong buổi kickoff Phase 3: tách khỏi `transform-job` vì `feast` kéo theo
  rất nhiều dependency nặng (protobuf, sqlalchemy, click, dask...) không
  liên quan tới DuckDB/PyIceberg.
- Đã kiểm tra tương thích trước khi code: `feast==0.66.0` cài sạch với
  `pandas==2.3.3`/`pyarrow==25.0.1` đã pin trong `libs/` — không xung đột
  version (rủi ro báo cáo kỹ thuật gốc mục 3 lo ngại không xảy ra).
- Service `redis` (`redis:7.4-alpine`, không bật persistence — chỉ là cache
  được `feast materialize` dựng lại từ Gold mỗi lần chạy) và
  `materialize-job` (profile `jobs`) thêm vào `docker-compose.yml`.
- `Makefile`: thêm `make user-features`.

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Đánh giá |
|---|---|---|
| Feature dạng rolling-window aggregate (ngầm định cho LightGBM) | Chuỗi item_id gần nhất theo user | Bắt buộc phải đổi theo ADR 0004 — model chính đổi từ LightGBM sang GRU |
| `build_interaction_features.py` | Không làm | Chốt trong kickoff Phase 3: nội dung mơ hồ, không cần thiết cho DoD, tránh xây đầu cơ |
| Không nêu rõ Parquet snapshot ghi ở đâu | MinIO (`feast-offline-store`) | Đúng nguyên tắc CLAUDE.md #6, nhất quán ADR 0003 |
| Không nêu rõ Feast registry lưu ở đâu | MinIO (`s3://feast-offline-store/registry.db`), không dùng default local file | Bug thật phát hiện khi test (mục 2.4) — bắt buộc phải sửa để hệ thống hoạt động đúng trong môi trường nhiều container |

Không cần thêm ADR mới cho các mục ở bảng trên (đăng ký registry trên
S3, tách image materialize) — đây là chi tiết triển khai nhất quán với
nguyên tắc đã có (CLAUDE.md #6, #2), không phải lệch công nghệ/kiến trúc.

## 4. Definition of Done — đối chiếu

Chạy thật trên `docker compose` (Lakekeeper + MinIO + Redis), không mock:

| Tiêu chí (từ `de-xuat-trien-khai.md`) | Trạng thái | Bằng chứng |
|---|---|---|
| `feast apply` chạy được trong `feature_repo/` | ✅ | `jobs.materialize.feast_materialize` chạy `feast apply` thành công, deploy 2 feature view |
| `feast materialize` đẩy dữ liệu vào Redis thành công | ✅ | `materialize-incremental` chạy không lỗi, log xác nhận 2 feature view materialized |
| Query 1 feature vector bằng Feast SDK trả đúng giá trị đã tính ở Gold | ✅ | `get_online_features()` từ container mới cho `user_id=u001` trả `item_sequence` khớp 100% với `gold.user_features`; `product_id=p001` trả `category=electronics`, `price=19.9` khớp `gold.item_features` |

**Kết luận: Phase 3 đạt Definition of Done, sẵn sàng cho Phase 4 (Training — GRU4Rec theo ADR 0004).**

## 5. Việc còn để lại cho phase sau

- `price` sau khi ép `Decimal→float64` có sai số biểu diễn nhị phân bình
  thường (`19.9` → `19.900000000000002`) — không ảnh hưởng chất lượng
  feature cho model, không cần xử lý thêm.
- Cold-start user (chưa từng tương tác) không có row trong
  `gold.user_features` — Phase 5 (serving) cần có nhánh fallback riêng khi
  `get_online_features()` trả về `None`/thiếu entity.
- `feature_store.yaml` hardcode hostname `redis`/`lakekeeper` trong mạng
  Docker — chỉ chạy được bên trong `reco-net`, không hỗ trợ chạy từ host
  (giống hạn chế đã ghi nhận với `ICEBERG_CATALOG_URI` ở Phase 2).
- Chưa tích hợp dữ liệu Amazon Fashion thật (người dùng nêu là việc "sau
  này", ADR 0004 mục Hệ quả) — khi làm, cần xem lại `MAX_SEQUENCE_LENGTH=10`
  có còn phù hợp không (dataset thật thường có nhiều interaction/user hơn
  seed giả lập hiện tại).
- `jobs/materialize/feast_materialize.py` dùng `materialize-incremental`
  chạy thủ công qua `docker compose run` — Phase 6 (Airflow) sẽ lịch hoá
  qua `dag_materialize.py` theo đúng kế hoạch gốc.

## 6. Danh sách file đã triển khai

### `libs/` (version 0.2.0) & SQL

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `libs/src/reco_mlops_libs/iceberg/schema_contracts/gold_user_features.py` | Contract `gold.user_features` — chuỗi hành vi/user | `MAX_SEQUENCE_LENGTH = 10`; dùng `pyiceberg.types.ListType` (kiểu list đầu tiên trong dự án) |
| `sql/gold/gold_user_features.sql` | Lấy 10 interaction gần nhất/user, `array_agg(... ORDER BY event_time ASC)` | Thứ tự chuỗi: cũ → mới (quy ước cho input GRU) |

### `jobs/features/` (chạy trong image `transform-job`)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/features/build_user_features.py` | Đọc `silver.interactions`, chạy SQL trên qua DuckDB, `overwrite()` vào `gold.user_features` | Chạy: `docker compose --profile jobs run --rm transform-job jobs.features.build_user_features` (hoặc `make user-features`) |

### `jobs/materialize/` (image `materialize` riêng)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/materialize/export_to_parquet.py` | Đọc `gold.item_features`/`gold.user_features` qua PyIceberg, ghi Parquet lên MinIO | Bucket `FEAST_S3_BUCKET` (mặc định `feast-offline-store`); tự cast cột Decimal → float64 |
| `jobs/materialize/feast_materialize.py` | `subprocess` gọi `feast apply` rồi `feast materialize-incremental <now>` | `cwd=feature_repo/`; yêu cầu binary `feast` có trong image |
| `infra/docker/materialize/Dockerfile` | Image riêng cho 2 job trên + `feature_repo/` | `ENTRYPOINT ["python","-m"]`, `CMD=["jobs.materialize.export_to_parquet"]` (đổi được lúc `run`) |
| `requirements/materialize.txt` | Dependency image materialize | `feast[redis,aws]==0.66.0` |

### `feature_repo/` (Feast repo chuẩn)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `feature_repo/entities.py` | Entity `user` (`user_id`), `product` (`product_id`) | `value_type=ValueType.STRING` (module `feast.value_type`, **không phải** `feast.types`) |
| `feature_repo/data_sources.py` | 2 `FileSource` trỏ Parquet trên MinIO | `path="s3://feast-offline-store/..."`, `timestamp_field="computed_at"` |
| `feature_repo/features_user.py` | `FeatureView user_recent_items` | `ttl=timedelta(days=3650)` (snapshot ghi đè, không có "hạn dùng" thật) |
| `feature_repo/features_item.py` | `FeatureView item_features` — map thẳng cột `gold.item_features` | `price`/`avg_rating` khai kiểu `Float64` |
| `feature_repo/feature_store.yaml` | Cấu hình Feast repo | `online_store: redis` (`redis:6379`), `registry: s3://feast-offline-store/registry.db` (**không phải** file cục bộ — xem mục 2.4) |

### Hạ tầng

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `docker-compose.yml` (service `redis`) | Online feature store cache | `redis:7.4-alpine`, không bật persistence (rebuild lại từ Gold mỗi lần materialize) |
| `docker-compose.yml` (service `materialize-job`) | Chạy `export_to_parquet`/`feast_materialize` qua profile `jobs` | Env `FEAST_S3_*` + `AWS_*` (Feast tự đọc biến AWS chuẩn, khác tên với `ICEBERG_S3_*`/`EXTRACT_S3_*`) |
| `infra/minio/init-buckets.sh` | (Mở rộng ở Phase 3) thêm bucket `feast-offline-store` | — |
| `.gitignore` (dòng `feature_repo/data/`) | Loại trừ artifact Feast sinh ra cục bộ (không dùng nữa từ khi registry chuyển sang S3, giữ lại phòng hờ) | — |
| `Makefile` (target `user-features`) | Chạy `build_user_features` qua `make` | — |
