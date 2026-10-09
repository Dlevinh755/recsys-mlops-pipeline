# Phase 2 — Lakehouse Bronze → Silver → Gold (Iceberg)

> Trạng thái: **Hoàn thành**. Đối chiếu với `de-xuat-trien-khai.md` mục
> "Phase 2". Phụ thuộc: Phase 0, Phase 1.

Thư mục code: `libs/src/reco_mlops_libs/iceberg/`,
`jobs/transform/`, `sql/`.

---

## 1. Mục tiêu ban đầu

Đây là phase rủi ro kỹ thuật cao nhất của dự án (PyIceberg commit + REST
Catalog). Mục tiêu: có 3 bảng Iceberg (bronze/silver/gold) truy vấn được qua
DuckDB, dữ liệu chạy xuyên suốt từ extract → gold không lỗi schema, test
tích hợp chạy trên hạ tầng thật (không mock).

## 2. Những gì đã triển khai

### 2.1 Kết nối Iceberg (`libs/src/reco_mlops_libs/iceberg/catalog.py`)

- `get_catalog()` — một điểm duy nhất tạo `pyiceberg.catalog.Catalog` (REST,
  trỏ vào Lakekeeper) cho mọi job transform, đọc cấu hình từ biến môi trường
  (`ICEBERG_CATALOG_URI`, `ICEBERG_WAREHOUSE_NAME`, `ICEBERG_S3_*`) qua
  `common/env.py` mới thêm.
- `ensure_namespace()` — tạo namespace Iceberg (`bronze`, `silver`, `gold`)
  nếu chưa tồn tại.
- Dependency: `pyiceberg[pyarrow,s3fs]==0.12.0`. Cần cả extra `pyarrow`
  **và** `s3fs` — Lakekeeper vend credential per-table (S3 vended
  credentials), PyIceberg dùng `fsspec`/`s3fs` cho đường ghi này, thiếu
  `s3fs` sẽ lỗi `ModuleNotFoundError` ngay khi `append()`/`overwrite()` (đã
  gặp và xử lý trong lúc triển khai).

### 2.2 Schema contracts (`libs/src/reco_mlops_libs/iceberg/schema_contracts/`)

5 contract, mỗi contract có cả `ICEBERG_SCHEMA` (pyiceberg `Schema`, dùng
để tạo bảng) và `PANDERA_SCHEMA` (pandera `DataFrameSchema`, dùng để
validate trước khi commit):

| Contract | Bảng Iceberg | Ghi chú |
|---|---|---|
| `bronze_products.py` | `bronze.products` | Giữ gần nguyên schema nguồn + 2 cột lineage `_extraction_run_id`, `_ingested_at` |
| `bronze_interactions.py` | `bronze.interactions` | Tương tự, thêm `EVENT_TYPES` dùng chung với Silver |
| `silver_products.py` | `silver.products` | Bỏ 2 cột lineage — Silver là state hiện tại, không phải lịch sử ingest |
| `silver_interactions.py` | `silver.interactions` | Tương tự |
| `gold_item_features.py` | `gold.item_features` | Aggregate theo `product_id` |

`de-xuat-trien-khai.md` chỉ liệt kê `bronze_interactions.py`,
`silver_interactions.py`, `gold_item_features.py` làm ví dụ minh hoạ ("khai
báo trước, kể cả khi transform chưa xong") — bản triển khai thêm
`bronze_products.py`/`silver_products.py` cho đối xứng, vì Bronze/Silver cần
xử lý cả hai nguồn (`products`, `interactions`) như nhau. Đây không phải một
lệch hướng kiến trúc, chỉ là lấp đầy phần tất yếu mà danh sách ví dụ chưa
liệt kê hết, nên không cần ADR.

### 2.3 Validator (`libs/src/reco_mlops_libs/iceberg/validators.py`)

`validate(df, schema, table_identifier)` — chạy `schema.validate(df,
lazy=True)`, gom **toàn bộ** dòng vi phạm vào một `SchemaContractViolation`
thay vì dừng ở lỗi đầu tiên (giúp sửa lỗi dữ liệu một lần thay vì từng dòng).
Input là `pandas.DataFrame` (không phải Polars) — xem mục 3 để biết lý do.

### 2.4 Iceberg writer dùng chung (`jobs/transform/iceberg_writer.py`)

- `get_or_create_table()` — tạo namespace + bảng nếu chưa có.
- `committed_run_ids()` — trả về tập `_extraction_run_id` đã có trong bảng
  Bronze, **đọc trực tiếp từ Iceberg**, không dùng bảng Postgres phụ để
  tracking. Đây là điểm mấu chốt cho idempotency — xem mục 2.5.
- `cast_to_table_schema()` — ép kiểu/thứ tự cột Arrow khớp đúng schema hiện
  tại của bảng Iceberg trước khi ghi.
- `append()` / `overwrite()` — Bronze dùng `append` (append-only, giữ lịch
  sử); Silver/Gold dùng `overwrite` (tính lại toàn bộ mỗi lần chạy).

### 2.5 `build_bronze.py` — idempotency không cần bảng bookkeeping thứ hai

Thay vì thêm bảng Postgres kiểu `pipeline.bronze_commits` (đã cân nhắc lúc
thiết kế), job đọc thẳng cột `_extraction_run_id` đã có trong Bronze để biết
run nào đã commit — đúng nguyên tắc CLAUDE.md #3 ("Iceberg là source of
truth duy nhất"): không có bước thứ hai nào có thể lệch pha với Iceberg, vì
không có bảng thứ hai. Một crash giữa lúc PyIceberg đang ghi để lại file mồ
côi trên MinIO (không có snapshot trỏ tới) nhưng không có dòng trùng — lần
chạy lại sẽ ghi lại an toàn.

**Phát hiện trong lúc test trên hạ tầng thật**: các run đã ghi nhận trong
`pipeline.extraction_runs` (từ trước khi đổi tên thư mục dự án) trỏ tới
Parquet cục bộ đã không còn tồn tại (`data/extract/` không có retention
guarantee — đúng như đã cảnh báo ở `docs/modules/phase-1-extract-watermark.md`
mục 6). Xử lý: `build_bronze.py` kiểm tra batch có tồn tại trước khi đọc;
nếu không, in cảnh báo dạng JSON (`status: skipped_missing_output`) và
**giữ nguyên run đó ở trạng thái pending** (không đánh dấu đã xử lý) thay vì
crash cả job hoặc coi như đã ingest — đây là lựa chọn có chủ đích, không
phải patch tạm: một job production không nên sập vì một batch lịch sử mất
dấu, nhưng cũng không được âm thầm bỏ qua dữ liệu.

**Cập nhật 2026-09-06**: nguyên nhân gốc (staging cục bộ không có retention)
đã được giải quyết ở [ADR 0003](../decisions/0003-extract-writes-directly-to-minio.md)
— `jobs/extract/run.py` giờ ghi thẳng lên MinIO (bucket `extract-staging`)
thay vì `data/extract/`. `build_bronze.py` cập nhật theo: đọc Parquet qua
`s3fs.S3FileSystem` + `pyarrow.dataset.dataset(..., filesystem=fs)`, kiểm
tra tồn tại bằng `fs.exists()` thay vì `Path.exists()`. Logic
`skipped_missing_output` vẫn giữ lại (không xoá) — không phải vì lỗ hổng cũ
còn đó, mà làm hàng phòng vệ chung cho mọi trường hợp một run trong Postgres
trỏ tới object đã biến mất trên MinIO (ví dụ xoá thủ công, lifecycle rule
tương lai), đúng nguyên tắc "không crash vì một batch lịch sử mất dấu,
nhưng cũng không âm thầm bỏ qua".

> **Cập nhật 2026-09-27**: mục 2.5 ở trên (`fetch_pending_runs()` đọc
> `pipeline.extraction_runs` trong Postgres, `PendingRun`,
> `skipped_missing_output`) **đã được thay thế** — xem
> [ADR 0006](../decisions/0006-dlt-native-incremental-extract.md) và mục 4.3
> (mới) bên dưới. Nguyên nhân: Phase 1 không còn ghi run vào Postgres nữa
> (dlt tự sở hữu state), nên không còn khái niệm "run Postgres trỏ tới object
> đã mất" — `build_bronze.py` giờ chỉ còn biết đến những gì thực sự đang nằm
> trên MinIO. Nội dung mục 2.5 giữ nguyên làm lịch sử.

### 2.6 `build_silver.py` / `build_gold.py` — SQL qua DuckDB, không qua Polars

Đọc toàn bộ Bronze/Silver bằng `table.scan().to_arrow()`, đăng ký vào
DuckDB (`con.register(...)`), chạy file `.sql` tương ứng
(`sql/silver/*.sql`, `sql/gold/*.sql`), lấy kết quả bằng
`con.execute(query).to_arrow_table()` (API `.arrow()` ở DuckDB 1.5.x trả về
`RecordBatchReader` chứ không phải `Table` — đã gặp lỗi này và sửa trong lúc
triển khai), validate, rồi `overwrite()` vào Silver/Gold.

- `sql/silver/silver_products.sql`, `sql/silver/silver_interactions.sql` —
  dedup bằng `ROW_NUMBER() OVER (PARTITION BY <pk> ORDER BY updated_at DESC,
  _ingested_at DESC)`, giữ bản mới nhất.
- `sql/gold/gold_item_features.sql` — `LEFT JOIN silver_interactions` theo
  `product_id`, đếm theo `event_type`, tính `avg_rating`, `distinct_users`,
  `last_interaction_at`.

Silver/Gold được **tính lại toàn bộ mỗi lần chạy** (không merge tăng dần) —
chấp nhận được ở quy mô dữ liệu MVP; đây là giới hạn đã biết trước (xem mục
6), không phải sơ suất.

Per-user features (`gold.user_features`) **không** làm ở phase này — đúng
theo `de-xuat-trien-khai.md`, đây là việc của `jobs/features/` ở Phase 3.

### 2.7 Hạ tầng

- `infra/docker/transform/Dockerfile`, `requirements/transform.txt` — một
  image `transform-job` duy nhất chạy cả 3 job (`build_bronze`,
  `build_silver`, `build_gold`) bằng cách đổi module ở `ENTRYPOINT ["python",
  "-m"]` / `CMD` — đúng gợi ý trong `cau-truc-project.md` ("extract/,
  transform/... build chung vào image transform").
- `docker-compose.yml` — thêm service `transform-job` (profile `jobs`,
  giống `extract-job`), phụ thuộc `catalog-bootstrap` hoàn tất trước khi
  chạy.
- `Makefile` — thêm `make bronze`, `make silver`, `make gold`,
  `make test-integration`.
- `.env.example` — thêm `ICEBERG_CATALOG_URI`, `ICEBERG_S3_ENDPOINT`,
  `ICEBERG_S3_REGION` (khoá/secret S3 tái dùng `MINIO_ROOT_USER/PASSWORD`
  đã có).
- `scripts/duckdb_query_iceberg.py` — script ad-hoc chứng minh DoD "truy vấn
  được qua DuckDB": lấy `metadata_location` hiện tại của bảng qua PyIceberg,
  rồi dùng `iceberg_scan()` của DuckDB đọc thẳng metadata.json trên MinIO —
  không cần DuckDB nói chuyện với REST Catalog.

### 2.8 Test tích hợp (`tests/integration/test_iceberg_writer.py`)

Chạy trên Lakekeeper + MinIO thật (`make test-integration`), dùng bảng
`bronze.it_test_products` / `silver.it_test_products` riêng (tự dọn dẹp sau
mỗi test) để không đụng vào bảng thật. 5 test:

1. Append hiển thị ngay + `committed_run_ids()` đúng.
2. Rerun với cùng `run_id` không tạo dòng trùng (đúng cơ chế
   `build_bronze.py` dùng).
3. `overwrite()` thay toàn bộ nội dung bảng, không cộng dồn.
4. `cast_to_table_schema()` sắp đúng thứ tự cột theo bảng Iceberg.
5. Contract từ chối dữ liệu vi phạm (`SchemaContractViolation`).

Phạm vi test **không** phụ thuộc vào việc Phase 1 đã chạy trước đó (khác với
việc chạy `make bronze/silver/gold` thật) — để bộ test tích hợp chạy lại
được nhiều lần một cách ổn định, thay vì phụ thuộc trạng thái
`pipeline.extraction_runs` vốn thay đổi liên tục.

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Đánh giá |
|---|---|---|
| Không nói rõ engine đọc Bronze/Silver khi build Silver/Gold | Đọc qua `PyIceberg.scan().to_arrow()` rồi mới đưa vào DuckDB chạy SQL, thay vì để DuckDB tự nói chuyện với REST Catalog | Chọn có chủ đích: giữ PyIceberg là con đường đọc/ghi Iceberg duy nhất (nhất quán, đã test kỹ ở Phase 2), DuckDB chỉ đóng vai trò compute engine chạy SQL thuần trên Arrow trong bộ nhớ — vẫn đúng "DuckDB là compute engine, PyIceberg là đường ghi trưởng thành hơn" (báo cáo kỹ thuật mục 3), chỉ thu hẹp thêm vai trò đọc cũng qua PyIceberg cho nhất quán. Không phải deviation về công nghệ, không cần ADR — nhưng ghi lại vì ảnh hưởng cách viết code các job sau. |
| Validator dùng "DataFrame" (không chỉ rõ pandas/Polars) | Dùng `pandas.DataFrame` làm định dạng trung gian để validate | pandera hỗ trợ pandas ổn định nhất; dữ liệu MVP nhỏ nên chi phí chuyển đổi Arrow↔pandas không đáng kể. Polars vẫn chưa cần dùng tới ở Phase 2 — sẽ cân nhắc lại nếu Phase 3+ cần thao tác DataFrame ngoài SQL ở quy mô lớn hơn. |
| Idempotency của Bronze không được đặc tả cụ thể | Dùng chính cột `_extraction_run_id` trong Iceberg làm nguồn sự thật duy nhất, không thêm bảng Postgres tracking | Chặt hơn yêu cầu tối thiểu, và đúng tinh thần "Iceberg source of truth" hơn là thêm một bảng bookkeeping thứ hai có thể lệch pha. |

Không có ADR nào được thêm cho Phase 2 — không có lựa chọn nào lệch khỏi
công nghệ/nguyên tắc đã chốt trong `bao-cao-ky-thuat.md`.

## 4. Bug đã sửa ở hạ tầng Phase 0 trong lúc triển khai Phase 2

Không phải scope Phase 2, nhưng chặn việc chạy job nên sửa luôn — ghi nhận
tại đây thay vì mở lại `phase-0-ha-tang-nen.md`:

- `infra/iceberg-catalog/bootstrap.sh` **không idempotent**: nếu Lakekeeper
  đã được bootstrap và warehouse `reco` đã tồn tại từ một lần `docker
  compose up` trước (volume Postgres/MinIO còn dữ liệu), script sẽ nhận HTTP
  400 (`CatalogAlreadyBootstrapped`, `CreateWarehouseStorageProfileOverlap`)
  và thoát lỗi — chặn mọi service phụ thuộc `catalog-bootstrap`. Đã sửa
  `post_json()` để coi 2 mã lỗi 400 cụ thể này là thành công khi tái chạy.
- `scripts/validate_phase0.py` và `tests/unit/test_package_metadata.py`
  từng hard-code `libs` phải đúng version `0.0.1` — đúng cho Phase 0 nhưng
  sai ngay khi Phase 2 tăng version hợp lệ. Đã nới thành: tên package đúng +
  version là semver hợp lệ (nguồn sự thật về version hiện tại là
  `libs/CHANGELOG.md`, không phải test này).

## 4.1 Bug phát hiện sau khi seed dữ liệu lớn hơn (2026-09-06)

Sau khi tăng dữ liệu mô phỏng lên 20 product/100 interaction (xem
`scripts/simulate_incremental_update.py`), `build_bronze.py` bắt đầu lỗi
`SchemaContractViolation: column 'rating' not in dataframe` — không xảy ra
với batch nhỏ trước đó.

**Nguyên nhân**: `dlt` tự suy luận kiểu dữ liệu từ nội dung batch; khi một
batch `interactions` không có dòng `event_type='rating'` nào (tức cột
`rating` toàn `NULL`), `dlt` không suy luận được kiểu và **bỏ hẳn cột đó**
khỏi Parquet thay vì ghi cột all-null — với trọng số 1/8 cho `rating` trong
`simulate_incremental_update.py`, một batch nhỏ có xác suất đáng kể rơi vào
tình huống này.

**Sửa**: thêm `jobs/transform/iceberg_writer.fill_missing_optional_columns()`
— trước khi validate, so khớp cột trong batch với `ICEBERG_SCHEMA` của
contract; cột nào **nullable** mà bị thiếu thì bù lại thành cột toàn `NULL`
đúng kiểu; cột **required** bị thiếu thì để nguyên (phải fail validation rõ
ràng, không che giấu vấn đề dữ liệu thật). Đây là hàng phòng vệ chung cho
mọi source, không riêng `rating`.

## 4.2 Bug phát hiện sau khi seed dữ liệu Amazon thật, gốc ở Phase 1 (2026-09-13)

Sau khi nạp dữ liệu Amazon thật qua `scripts/seed_source_db.py` (16,222
interactions — vượt xa `EXTRACT_BATCH_SIZE=1000`), `build_bronze.py` chỉ
commit được 230/16230 dòng `interactions` và 565/1565 dòng `products` — âm
thầm, không lỗi, không cảnh báo.

**Nguyên nhân nằm ở Phase 1** (`jobs/extract/watermark.py::commit_batch`):
một run có thể gồm nhiều batch nội bộ, nhưng cột `output_path` trong
`pipeline.extraction_runs` bị **ghi đè** mỗi lần commit thay vì cộng dồn —
`build_bronze.py` chỉ đọc được path của batch cuối. Phân tích đầy đủ, các
phương án đã cân nhắc, và cách sửa (`extraction_runs.output_path` → cột
`batches` JSONB dạng list, append-only) nằm ở
[`phase-1-extract-watermark.md` mục 4](phase-1-extract-watermark.md#4-bug-phát-hiện-và-sửa-mất-dữ-liệu-khi-1-run-có-nhiều-batch-nội-bộ-2026-09-13)
— ghi ở đây vì cách sửa cũng đụng tới `build_bronze.py`/`iceberg_writer.py`
của Phase 2.

Phần thuộc Phase 2: `build_bronze.py` đổi `PendingRun.output_path: str` →
`output_paths: list[str]`, đọc **toàn bộ** batch của 1 run trước khi coi run
đó "đã xử lý". Đọc nhiều batch cùng lúc lộ thêm 1 vấn đề nhỏ: 2 batch trong
cùng 1 run có thể khác nhau ở thứ tự cột hoặc kiểu Arrow (`string` vs
`large_string` khi 1 batch có cột toàn `NULL`, tương tự mục 4.1 nhưng ở đây
là khác nhau **giữa 2 batch** chứ không phải thiếu cột) — `pa.concat_tables`
không chấp nhận. Thêm `iceberg_writer.cast_to_arrow_schema()` (factor lại từ
`cast_to_table_schema()` sẵn có) để ép mọi batch về đúng
`contract.ICEBERG_SCHEMA` trước khi gộp.

Kiểm chứng lại từ cold start hoàn toàn: `bronze.products` = 1565/1565,
`bronze.interactions` = 16230/16230 (trước đó lần lượt 565 và 230); rerun
`build_bronze` lần 2 → `rows_committed: 0` cả 2 bảng.

## 4.3 Bug thứ hai cùng họ, gốc cũng ở Phase 1 (2026-09-27) — dẫn tới bỏ hẳn bookkeeping Postgres

Khác biến thể mục 4.2 (mất batch **trong** một run `succeeded`), lần này là
mất batch **của một run `failed`**: `commit_batch()` (Phase 1, thiết kế cũ)
tiến watermark ngay sau mỗi batch nội bộ ghi thành công lên MinIO, độc lập
với kết quả cuối của cả run — nhưng `fetch_pending_runs()` (Phase 2) chỉ đọc
run có `status = 'succeeded'`. Một run bị crash giữa chừng (do lỗi
filesystem khi bind-mount `data/dlt-pipelines` trên Windows) đã ghi thành
công 14 batch nội bộ (~12.992/16.230 dòng `interactions`) lên MinIO, nhưng
`status = 'failed'` khiến `build_bronze.py` không bao giờ nạp chúng vào
Bronze — watermark đã đi qua (không extract lại) nhưng Bronze thiếu vĩnh
viễn 80% dữ liệu, không exception, không cảnh báo.

**Sửa tận gốc ở Phase 1** (không phải vá cục bộ ở Phase 2 như mục 4.2): bỏ
hẳn watermark + audit run tự viết trong Postgres, chuyển sang
`dlt.sources.incremental` — dlt tự cột chặt cursor vào kết quả TOÀN BỘ
`pipeline.run()`, không có khái niệm "batch đã ghi nhưng run cha fail". Chi
tiết đầy đủ ở
[`phase-1-extract-watermark.md` mục 8](phase-1-extract-watermark.md#8-refactor-sang-dlt-native-incremental-2026-09-27)
và [ADR 0006](../decisions/0006-dlt-native-incremental-extract.md).

Phần thuộc Phase 2: `build_bronze.py` viết lại hoàn toàn — bỏ tham số
Postgres, `PendingRun`, `fetch_pending_runs()`, khái niệm
`skipped_missing_output`. Thay bằng: đọc toàn bộ dataset staging của 1
nguồn qua `pyarrow`/`s3fs` (từng file rồi `cast_to_arrow_schema()` +
`pa.concat_tables()`, tái dùng đúng cơ chế chuẩn hoá schema từ mục 4.1/4.2 —
lý do tương tự: các file parquet khác nhau vẫn có thể lệch schema ở cột
toàn `NULL`), lọc theo `_dlt_load_id` (dlt tự thêm vào mọi dòng) đã có trong
Bronze. `committed_run_ids()` (`iceberg_writer.py`) **không đổi code**, chỉ
đổi ý nghĩa giá trị trả về (dlt load id thay vì Postgres run_id UUID) — vẫn
đúng nguyên tắc "Iceberg là nguồn sự thật duy nhất" đã nêu ở mục 2.5, giờ
còn triệt để hơn vì không còn bảng Postgres nào để lệch pha với nó nữa.

Kiểm chứng lại từ cold start hoàn toàn (bao gồm kịch bản
`EXTRACT_FAIL_AFTER_ROWS` mô phỏng đúng crash đã gặp): `bronze.interactions`
= 16230/16230, `bronze.products` = 1565/1565; rerun `build_bronze` lần 2 →
`rows_committed: 0` cả 2 bảng.

## 4.4 Thêm `image_url` vào `products`/`item_features` (2026-09-28)

Không phải bug — bổ sung tính năng: `amazone_data/prepare_seed_data.py` vốn
đọc trường `images` gốc của Amazon chỉ để lọc (`has_image`) rồi bỏ; sửa lại
để giữ URL ảnh thật (`image_url`, ưu tiên `large` > `hi_res` > `thumb`),
dùng hiển thị ảnh sản phẩm trên `ui/` (Phase 5). Không có tài liệu gốc nào
(`de-xuat-trien-khai.md`/`bao-cao-ky-thuat.md`) đề cập ảnh sản phẩm nên
không phải deviation, không cần ADR — xem `libs/CHANGELOG.md` 0.5.0.

Lan truyền: cột `image_url` (nullable) thêm vào Postgres `products`,
`bronze.products`, `silver.products`, `gold.item_features` (schema
contract + `sql/silver/silver_products.sql` + `sql/gold/gold_item_features.sql`).
`build_bronze.py`/`build_silver.py`/`build_gold.py` không đổi code — hoàn
toàn driven bởi schema contract (đúng thiết kế mục 2.2), chỉ cần thêm cột
vào 3 file schema contract + 2 file SQL.

**Áp dụng lên dữ liệu/bảng đã tồn tại** (không phải cold start): `ALTER
TABLE products ADD COLUMN image_url TEXT` + `UPDATE ... SET image_url =
..., updated_at = CURRENT_TIMESTAMP` (backfill từ `meta.parquet`, bump
`updated_at` để cursor `dlt` ở Phase 1 tự nhận diện là hàng đã đổi, không
cần reset state thủ công) trên Postgres; 3 bảng Iceberg
(`bronze.products`, `silver.products`, `gold.item_features`) đã tồn tại từ
trước cũng không tự thêm cột khi schema contract đổi (`get_or_create_table`
chỉ tạo mới, không migrate bảng đã có) — phải chạy
`table.update_schema().add_column(...)` (PyIceberg schema evolution, không
xoá dữ liệu) một lần thủ công cho cả 3 bảng trước khi chạy lại
extract → bronze → silver → gold.

Sau đó `gold.item_features` thêm tiếp cột `title` (required, libs 0.6.0):
bảng đã tồn tại nên phải `update_schema(allow_incompatible_changes=True)`
để thêm cột required — an toàn vì `build_gold` ghi đè toàn bộ bảng ngay sau đó.

## 5. Definition of Done — đối chiếu

Chạy thật trên `docker compose` (Lakekeeper + MinIO + Postgres), không mock:

| Tiêu chí (từ `de-xuat-trien-khai.md`) | Trạng thái | Bằng chứng |
|---|---|---|
| 3 bảng Iceberg (bronze/silver/gold) tồn tại | ✅ | `bronze.products`, `bronze.interactions`, `silver.products`, `silver.interactions`, `gold.item_features` — 5 bảng (2 nguồn × 2 tầng + 1 gold) |
| Truy vấn được qua DuckDB | ✅ | `scripts/duckdb_query_iceberg.py bronze.products` / `silver.interactions` / `gold.item_features` trả kết quả đúng qua `iceberg_scan()` |
| Dữ liệu chạy xuyên suốt extract → gold không lỗi schema | ✅ | Kiểm chứng lại 2026-09-06 từ **cold start hoàn toàn** (`docker compose down --volumes`, xoá `data/`, dựng lại từ Phase 0): `extract-job` → `build_bronze` → `build_silver` → `build_gold` chạy nối tiếp thành công, ra `bronze/silver`=25 product + 108 interaction, `gold.item_features`=25 dòng — không còn `runs_skipped_missing_output` nào (khác lần kiểm thử trước, vốn có 2 batch mồ côi từ trạng thái cũ) |
| `pytest tests/integration/` pass | ✅ | 5/5 test pass, chạy trong container `transform-job` gắn mạng thật |
| Rerun `build_bronze.py` với dữ liệu incremental mới không tạo trùng | ✅ | Rerun sau cold-start run: `rows_committed: 0`, `runs_skipped_missing_output: 0` cho cả 2 bảng |
| Một run có nhiều batch nội bộ không bị mất dữ liệu ở Bronze | ✅ (bổ sung 2026-09-13, xem mục 4.2) | Với dataset Amazon thật (17 batch/run): `bronze.interactions` = 16230/16230, `bronze.products` = 1565/1565 |
| Một run bị crash/fail giữa chừng không được để lửng dữ liệu (đã ghi nhưng không nạp được) | ✅ (bổ sung 2026-09-27, xem mục 4.3) | Sau refactor sang dlt-native incremental (ADR 0006): `EXTRACT_FAIL_AFTER_ROWS` mô phỏng crash → không gì lửng trên MinIO; `bronze.interactions` = 16230/16230 sau khi chạy lại |

**Kết luận: Phase 2 đạt Definition of Done, sẵn sàng cho Phase 3 (Feature Store).**

## 6. Việc còn để lại cho phase sau

- **Silver/Gold tính lại toàn bộ mỗi lần chạy** (`overwrite`, không merge
  tăng dần) — chấp nhận được ở quy mô MVP nhưng sẽ chậm dần khi Bronze lớn
  lên. Đúng như báo cáo kỹ thuật mục 11 đã ghi nhận giới hạn của DuckDB khi
  dữ liệu lớn hơn nhiều so với MVP; hướng khắc phục (merge tăng dần hoặc
  chuyển engine) không cần thiết trong phạm vi khóa luận này.
- ~~Dữ liệu Bronze hiện chưa đầy đủ vì một số batch lịch sử đã mất file
  Parquet cục bộ...~~ — không còn là vấn đề: sau khi seed dữ liệu Amazon thật
  và sửa bug mục 4.2, `bronze.products`/`bronze.interactions` đã đầy đủ
  100% (1565/16230 dòng), đủ khối lượng cho Phase 3/4.
- `jobs/maintenance/compact_iceberg.py` — cố ý dời sang Phase 7
  (`dag_maintenance.py`), đúng như `de-xuat-trien-khai.md` cho phép ("có thể
  làm cuối phase này hoặc dời sang Phase 7").
- `libs/src/reco_mlops_libs/common/config.py`, `logging.py` — chưa thêm.
  Phase 2 chỉ cần vài biến môi trường đơn giản (`common/env.py` đã đủ) và
  logging vẫn theo lối `print(json.dumps(...))` như Phase 1; sẽ cân nhắc lại
  nếu Phase 3+ có nhu cầu logging/config phức tạp hơn thật sự (tránh trừu
  tượng hoá sớm khi chưa có use case).

## 7. Danh sách file đã triển khai

### `libs/` (package dùng chung, version 0.1.0)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `libs/src/reco_mlops_libs/common/env.py` | Helper đọc biến môi trường bắt buộc/tuỳ chọn | `require_env()`, `get_env()`, `get_int_env()` |
| `libs/src/reco_mlops_libs/iceberg/catalog.py` | Kết nối Lakekeeper REST Catalog + MinIO, dùng chung mọi job transform | Đọc `ICEBERG_CATALOG_URI`, `ICEBERG_WAREHOUSE_NAME`, `ICEBERG_S3_ENDPOINT/ACCESS_KEY/SECRET_KEY/REGION` |
| `libs/src/reco_mlops_libs/iceberg/schema_contracts/bronze_products.py` | Contract Bronze cho `products` | `ICEBERG_SCHEMA` (pyiceberg) + `PANDERA_SCHEMA` (pandera) |
| `libs/src/reco_mlops_libs/iceberg/schema_contracts/bronze_interactions.py` | Contract Bronze cho `interactions` | Có `EVENT_TYPES` dùng chung với Silver |
| `libs/src/reco_mlops_libs/iceberg/schema_contracts/silver_products.py` | Contract Silver — 1 dòng/`product_id`, bỏ cột lineage | `unique=True` trên `product_id` |
| `libs/src/reco_mlops_libs/iceberg/schema_contracts/silver_interactions.py` | Contract Silver — 1 dòng/`interaction_id` | `unique=True` trên `interaction_id` |
| `libs/src/reco_mlops_libs/iceberg/schema_contracts/gold_item_features.py` | Contract Gold — aggregate theo `product_id` | 13 cột: `num_*`, `avg_rating`, `distinct_users`, `last_interaction_at`, `computed_at` |
| `libs/src/reco_mlops_libs/iceberg/validators.py` | Enforce schema contract trước khi commit Iceberg | `validate(df, schema, table_identifier)`, raise `SchemaContractViolation` (gom hết dòng lỗi, không dừng ở lỗi đầu) |

### `jobs/transform/` (image `transform-job`)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/transform/iceberg_writer.py` | Helper commit PyIceberg dùng chung: `get_or_create_table`, `committed_run_ids`, `cast_to_arrow_schema`, `cast_to_table_schema`, `append`, `overwrite`, `fill_missing_optional_columns` | `committed_run_ids()` đọc trực tiếp cột `_extraction_run_id` từ Iceberg — giá trị nay là dlt `_dlt_load_id` (2026-09-27, mục 4.3/ADR 0006), không phải Postgres run_id; `cast_to_arrow_schema()` thêm 2026-09-13 (mục 4.2) để chuẩn hoá schema nhiều batch trước khi `concat_tables` |
| `jobs/transform/build_bronze.py` | Đọc toàn bộ dataset Parquet trên MinIO (`extract-staging/staging/<source>`), lọc theo `_dlt_load_id` chưa commit, validate, `append()` vào `bronze.products`/`bronze.interactions` | Viết lại 2026-09-27 (mục 4.3/ADR 0006): bỏ hẳn Postgres/`PendingRun`/`skipped_missing_output`; đọc env `EXTRACT_S3_*` (không còn `SOURCE_DB_DSN`) |
| `jobs/transform/build_silver.py` | Dedup Bronze → Silver qua SQL DuckDB, `overwrite()` toàn bộ mỗi lần chạy | Chạy `sql/silver/*.sql` |
| `jobs/transform/build_gold.py` | Aggregate Silver → `gold.item_features` qua SQL DuckDB | Chạy `sql/gold/gold_item_features.sql` |

### SQL & test & script

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `sql/silver/silver_products.sql`, `silver_interactions.sql` | Dedup bằng `ROW_NUMBER() OVER (PARTITION BY <pk> ORDER BY updated_at DESC)` | SQL thuần, không đặc thù DuckDB |
| `sql/gold/gold_item_features.sql` | `LEFT JOIN` interactions theo `product_id`, đếm/aggregate theo `event_type` | — |
| `tests/integration/test_iceberg_writer.py` | Test PyIceberg commit thật (append/overwrite/idempotent/validator) trên bảng `*.it_test_*` riêng | Chạy bằng `make test-integration`; tự dọn dẹp sau mỗi test |
| `scripts/duckdb_query_iceberg.py` | Query ad-hoc 1 bảng Iceberg qua DuckDB (`iceberg_scan` trên metadata.json) | Đối số: `<namespace.table> [sql]`; cần biến `ICEBERG_S3_*` |

### Hạ tầng & bug fix

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `infra/docker/transform/Dockerfile` | Image chung cho `build_bronze`/`build_silver`/`build_gold`/`build_user_features` (Phase 3) | `ENTRYPOINT ["python","-m"]`, `CMD` đổi được lúc `docker compose run` |
| `requirements/transform.txt` | Dependency image transform | `psycopg`, `duckdb`, `s3fs`, `pytest` |
| `docker-compose.yml` (service `transform-job`) | Chạy job transform qua profile `jobs` | Env `ICEBERG_*` + `EXTRACT_S3_*`; `depends_on: catalog-bootstrap` |
| `.dockerignore` (dòng `!tests/integration`) | Cho phép COPY `tests/integration/` vào image dù `tests/` bị ignore | — |
| `infra/iceberg-catalog/bootstrap.sh` | (Sửa ở Phase 2) Idempotent hoá khi catalog/warehouse đã tồn tại | Xem mục 4 |
