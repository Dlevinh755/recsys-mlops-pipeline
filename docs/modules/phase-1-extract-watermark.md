# Phase 1 — Extract & cơ chế watermark

> Trạng thái: **Hoàn thành**. Đối chiếu với `de-xuat-trien-khai.md` mục
> "Phase 1". Phụ thuộc: Phase 0.
>
> **Cập nhật 2026-09-06**: đích ghi output đã đổi từ Parquet cục bộ
> (`data/extract/`, mô tả gốc bên dưới) sang ghi thẳng lên MinIO — xem
> [ADR 0003](../decisions/0003-extract-writes-directly-to-minio.md). Mục
> 2.2 và 2.5 dưới đây đã cập nhật theo trạng thái hiện tại; phần còn lại
> giữ nguyên vì vẫn đúng.
>
> **Cập nhật 2026-09-27**: cơ chế watermark tự viết trong Postgres mô tả ở
> mục 2.1/2.2/2.3 và bug ở mục 4 **đã được thay thế hoàn toàn** bởi
> `dlt.sources.incremental` — xem
> [ADR 0006](../decisions/0006-dlt-native-incremental-extract.md) và mục 8
> (mới) bên dưới. Nội dung gốc bên dưới giữ nguyên làm lịch sử (vẫn đúng về
> việc đã từng triển khai và bug đã từng xảy ra) nhưng **không còn phản ánh
> code hiện tại** — đọc mục 8 để biết trạng thái thật.

Thư mục code: `jobs/extract/`

---

## 1. Mục tiêu ban đầu

Chứng minh cơ chế incremental extract hoạt động đúng trên dữ liệu thật
(watermark), **trước khi động đến Iceberg** — output tạm thời có thể ghi ra
Parquet cục bộ.

## 2. Những gì đã triển khai

### 2.1 Cơ chế watermark (`jobs/extract/watermark.py`)

Khác với thiết kế gốc chỉ nêu "`updated_at` lớn nhất", bản triển khai dùng
**cursor kép `(updated_at, primary_key)`** để đảm bảo tổng thứ tự (total
order) ngay cả khi nhiều bản ghi có cùng `updated_at`:

- `SourceSpec` — khai báo mỗi nguồn: tên bảng, cột khoá chính, kiểu dữ liệu
  khoá chính (cần thiết vì so sánh cursor phải cast đúng kiểu trong SQL).
- `Cursor(updated_at, primary_key)` — điểm dừng của lần chạy trước.
- `build_incremental_query()` — sinh câu SQL:
  - Lần chạy đầu (`cursor.is_initial`): `SELECT * FROM <table> ORDER BY
    updated_at, <pk> LIMIT %s`.
  - Lần sau: `WHERE (updated_at, <pk>) > (%s, %s::<pk_type>) ORDER BY
    updated_at, <pk> LIMIT %s` — dùng **row comparison** của Postgres, tránh
    bỏ sót/lặp bản ghi khi trùng `updated_at`.
- Watermark được lưu bền vững trong Postgres (`pipeline.extraction_watermarks`,
  1 dòng/nguồn), không lưu trong container — đúng nguyên tắc thiết kế.
- `pipeline.extraction_runs` — ghi lại **từng lần chạy** của từng nguồn: cursor
  đầu/cuối, số dòng, đường dẫn output, trạng thái (`running` /
  `succeeded` / `failed`), thông điệp lỗi. Đây là bảng audit không có trong
  bản thiết kế gốc, giúp truy vết lịch sử extract.

### 2.2 Idempotency khi retry (`jobs/extract/dlt_writer.py`)

Đây là phần vượt so với mô tả trong `de-xuat-trien-khai.md` (chỉ ghi "dùng
dlt, query WHERE updated_at > watermark"):

- Mỗi batch có một **id xác định** (`batch_identity`) tính bằng SHA-256 từ
  `(source, cursor đầu, cursor cuối)` — cùng một khoảng dữ liệu luôn ra cùng
  id.
- `dlt` pipeline dùng `write_disposition="replace"` ghi vào dataset đặt tên
  theo batch id, xuất Parquet qua `dlt.destinations.filesystem` — **thẳng
  lên MinIO** (`s3://extract-staging/<dataset>`, xem ADR 0003), không còn
  qua đĩa cục bộ như bản gốc.
- Hệ quả: nếu job chết **sau khi** dlt ghi xong Parquet nhưng **trước khi**
  Postgres commit watermark mới, lần retry sẽ tính ra đúng batch id cũ và
  **ghi đè (replace)** đúng object đó trên MinIO — không tạo dữ liệu trùng,
  không cần logic dọn dẹp riêng. Cơ chế idempotent không đổi khi chuyển từ
  đĩa cục bộ sang S3 — đã kiểm chứng lại bằng kịch bản
  `EXTRACT_FAIL_AFTER_WRITE`.
- `EXTRACT_FAIL_AFTER_WRITE` (biến môi trường) — cơ chế injection lỗi có chủ
  đích, dùng để test kịch bản "chết giữa chừng" nêu trên mà không cần giả lập
  crash thật.

### 2.3 Entrypoint (`jobs/extract/run.py`)

- Chạy tuần tự 2 nguồn: `products` rồi `interactions`.
- Với mỗi nguồn: lặp `fetch_batch → load qua dlt → commit_batch` cho tới khi
  hết dữ liệu mới (rỗng thì dừng).
- Mỗi lần `fetch_batch` thành công đều gọi `commit_batch` ngay (không gom hết
  rồi mới commit một lần) — giảm lượng dữ liệu phải xử lý lại nếu lỗi ở giữa
  một nguồn có nhiều batch.
- In log dạng JSON structured cho từng batch và cho kết quả cuối
  (`execution_id`, danh sách `run_id` theo nguồn, tổng số dòng) — dễ audit
  qua log thay vì chỉ qua DB.

### 2.4 Khai báo nguồn dữ liệu

- `jobs/extract/sources/products.py` — `SourceSpec(name="products",
  primary_key="product_id", primary_key_type="text")`.
- `jobs/extract/sources/interactions.py` — `SourceSpec(name="interactions",
  primary_key="interaction_id", primary_key_type="bigint")`.

### 2.5 Hạ tầng & script hỗ trợ

- `infra/docker/extract/Dockerfile`, `requirements/extract.txt` — image
  riêng cho job extract (chạy qua Compose profile `jobs`, không chạy cùng
  hạ tầng nền).
- `scripts/run_job_locally.sh extract` — chạy job ngoài Airflow (Airflow
  chưa tồn tại ở giai đoạn này, đúng nguyên tắc "test job độc lập trước").
- `scripts/simulate_incremental_update.py` — mô phỏng cập nhật sản phẩm +
  2 interaction cùng timestamp, dùng để kiểm chứng cursor kép xử lý đúng
  trường hợp trùng `updated_at`.

### 2.6 Test

- `tests/unit/extract/test_watermark.py` — unit test cho logic build query
  và cursor.
- Kiểm thử thủ công đã mô tả trong `README.md`: chạy `run.py` hai lần liên
  tiếp xen giữa `simulate_incremental_update.py`, xác nhận lần hai chỉ lấy
  đúng phần mới; kiểm thử riêng kịch bản fail-after-write bằng
  `EXTRACT_FAIL_AFTER_WRITE`.

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Đánh giá |
|---|---|---|
| Watermark theo `updated_at` đơn | Cursor kép `(updated_at, primary_key)` | Chặt chẽ hơn — tránh lỗi bỏ sót/lặp khi nhiều bản ghi cùng `updated_at` (tình huống thực tế phổ biến, đặc biệt sau `simulate_incremental_update.py` hoặc `/interact` ghi hàng loạt) |
| Không nêu rõ cơ chế chống trùng khi retry | Batch id xác định + `write_disposition="replace"` | Giải quyết đúng rủi ro "job lỗi giữa chừng" mà bản thiết kế mới nêu ở mức nguyên tắc, chưa có cơ chế cụ thể |
| Không có bảng audit run | `pipeline.extraction_runs` | Bổ sung khả năng quan sát (observability) sớm, có ích khi debug và khi trình bày cơ chế idempotent lúc bảo vệ |

## 4. Bug phát hiện và sửa: mất dữ liệu khi 1 run có nhiều batch nội bộ (2026-09-13)

**Phát hiện khi nào**: sau khi seed dữ liệu Amazon thật (16,222 interactions —
xem `scripts/seed_source_db.py`), `EXTRACT_BATCH_SIZE=1000` khiến 1 lần chạy
`run.py` cho nguồn `interactions` tạo ra **17 batch nội bộ** thay vì 1 như
mọi lần test trước đó (data giả trước giờ luôn dưới 1000 dòng/nguồn/lần
chạy). `jobs/transform/build_bronze.py` (Phase 2) chỉ nạp được batch **cuối
cùng** của mỗi run (230/16230 dòng `interactions`, 565/1565 dòng `products`)
— lộ ra bug đã tồn tại sẵn trong code Phase 1, chỉ là chưa từng bị kích hoạt.

**Nguyên nhân gốc** (`jobs/extract/run.py` + `jobs/extract/watermark.py`):
`run_id = uuid4()` được tạo **một lần cho cả execution** của 1 nguồn (đúng
thiết kế — xem mục 2.3), nhưng vòng lặp `while True` gọi `commit_batch()`
**mỗi khi có 1 batch nội bộ mới**, và `commit_batch()` chạy
`UPDATE ... SET output_path = %s WHERE run_id = %s` — **ghi đè**, không cộng
dồn. Vì tất cả các lần gọi trong cùng 1 run dùng chung `run_id`, mỗi lần
commit sau xoá mất `output_path` của batch trước. `extracted_rows` vẫn đúng
(là tổng cộng dồn qua tham số `total` của `run.py`, không lấy từ DB), nên chỉ
số này che giấu việc `output_path`/`dlt_load_ids` đã mất dữ liệu — đây là lý
do bug không bị phát hiện qua log, chỉ lộ ra khi đối chiếu số dòng thật trong
Bronze.

**Vì sao trước giờ không phát hiện**: `seed_data.sql` (8 dòng) và
`simulate_incremental_update.py` (mặc định 100 dòng/lần) luôn dưới
`EXTRACT_BATCH_SIZE`, nên mọi lần test trong DoD gốc chỉ tạo đúng 1 batch nội
bộ/run — không bao giờ kích hoạt tình huống ghi đè.

**Đã cân nhắc 2 hướng sửa** (xem thảo luận đầy đủ trong lịch sử trao đổi khi
sửa; tóm tắt ở đây để tra cứu):

1. Đổi `run_id` thành cấp phát mỗi batch (1 row/batch trong
   `pipeline.extraction_runs`).
2. **[Đã chọn]** Giữ nguyên 1 row/run, đổi cách lưu output từ 1 giá trị
   scalar thành **danh sách**.

Lựa chọn 2 khớp với cách các hệ thống ETL production (Airbyte, Singer/dlt,
Debezium) track "run"/"attempt": ở granularity con người quan tâm (1 lần
job chạy = 1 record trạng thái/tổng số dòng), còn chi tiết phân trang nội bộ
(phụ thuộc tham số tuỳ chỉnh `EXTRACT_BATCH_SIZE`) nằm **bên trong** record
đó dưới dạng cấu trúc, không nổ thành nhiều record độc lập. Lựa chọn 1 sẽ
khiến số dòng trong bảng audit phụ thuộc vào một tham số tuning thuần kỹ
thuật, không có ý nghĩa nghiệp vụ.

**Sửa cụ thể**:

- `infra/source-db/init/schema.sql` + `jobs/extract/watermark.py
  ::ensure_metadata_tables()`: xoá 3 cột `output_path TEXT`,
  `dlt_pipeline_name TEXT`, `dlt_load_ids JSONB`, thay bằng 1 cột
  `batches JSONB NOT NULL DEFAULT '[]'::jsonb` — mỗi phần tử
  `{"output_path", "dlt_pipeline_name", "dlt_load_ids"}` ứng với 1 batch nội
  bộ.
- `commit_batch()`/`fail_run()`: `batches = batches || %s::jsonb` (nối thêm)
  thay vì overwrite. `run.py` **không cần sửa** — vẫn gọi `commit_batch` với
  cùng tham số như cũ, chỉ khác hành vi bên trong.
- `jobs/transform/build_bronze.py`: `PendingRun.output_path: str` →
  `output_paths: list[str]`; `fetch_pending_runs()` đọc `batches` (list)
  thay vì `output_path IS NOT NULL`; `build_bronze_table()` đọc **toàn bộ**
  batch của 1 run trước khi coi run đó đã xử lý xong (đọc thiếu 1 batch nào
  cũng giữ nguyên trạng thái pending, không đánh dấu committed một phần).
- Hệ quả phụ cần xử lý thêm: 2 batch trong cùng 1 run có thể có schema Arrow
  hơi khác nhau (thứ tự cột, hoặc `string` vs `large_string` khi 1 batch có
  cột toàn `NULL` — xem mục 4.1 `phase-2-lakehouse.md`), khiến
  `pa.concat_tables()` lỗi. Thêm `iceberg_writer.cast_to_arrow_schema()`
  (factor lại từ `cast_to_table_schema()` đã có) để chuẩn hoá schema từng
  batch về đúng `contract.ICEBERG_SCHEMA` trước khi gộp.

**Đã kiểm chứng lại từ cold start hoàn toàn** (`docker compose down
--volumes` → `up` → auto-seed → `make extract` → `make bronze`):

| Bảng | Trước khi sửa | Sau khi sửa | Đối chiếu |
|---|---|---|---|
| `pipeline.extraction_runs` (products) | 1 batch trong `output_path` | 2 batch trong `batches` | Khớp `EXTRACT_BATCH_SIZE=1000` trên 1565 dòng |
| `pipeline.extraction_runs` (interactions) | 1 batch | 17 batch | Khớp 1000×16 + 230 = 16230 |
| `bronze.products` | 565/1565 dòng | **1565/1565** | Đúng 100% |
| `bronze.interactions` | 230/16230 dòng | **16230/16230** | Đúng 100% |
| Rerun `build_bronze` lần 2 | — | `rows_committed: 0` cả 2 bảng | Idempotent giữ nguyên |

## 5. Definition of Done — đối chiếu

| Tiêu chí (từ `de-xuat-trien-khai.md`) | Trạng thái |
|---|---|
| Chạy `run_job_locally.sh extract` 2 lần, dữ liệu nguồn đổi giữa 2 lần, ra 2 batch không trùng/không sót | ✅ — đảm bảo bởi cursor kép + batch id xác định |
| Output tạm ghi Parquet cục bộ, chưa cần Iceberg | ✅ ban đầu (ghi `data/extract/`); từ 2026-09-06 ghi thẳng MinIO thay vì cục bộ — xem ADR 0003, vẫn "chưa cần Iceberg" đúng tinh thần Phase 1 |
| Một run tạo nhiều batch nội bộ (>1 lần `EXTRACT_BATCH_SIZE`) không được làm mất batch nào | ✅ (bổ sung 2026-09-13, xem mục 4) — kiểm chứng với dataset Amazon thật: 17 batch/run cho `interactions`, `bronze.interactions` nhận đủ 16230/16230 dòng |

**Kết luận: Phase 1 đạt Definition of Done, sẵn sàng cho Phase 2 (Bronze/Silver/Gold trên Iceberg).**

## 6. Việc còn để lại cho phase sau

- ~~Dữ liệu Parquet ở `data/extract/` hiện là đầu ra tạm thời...~~ — đã giải
  quyết bằng ADR 0003 (extract ghi thẳng MinIO). `jobs/transform/
  build_bronze.py` vẫn là bên đọc dữ liệu này để nối vào Iceberg Bronze,
  chỉ khác nguồn đọc (MinIO thay vì đĩa cục bộ).
- ~~Chưa có `scripts/seed_source_db.py` nạp dữ liệu Amazon review thật...~~ —
  đã bổ sung (2026-09-13): `scripts/seed_source_db.py` nạp
  `amazone_data/data_split/train.parquet` + `meta.parquet` (~1.6K sản phẩm,
  ~1.6K user, ~16.2K interaction thật từ Amazon Reviews 2023) vào
  `products`/`users`/`interactions`. Có 2 chế độ: (1) nạp thẳng vào
  `source-db` đang chạy, idempotent (dedupe theo `(user_id, product_id)` cho
  `event_type='rating'`); (2) `--dump-sql` sinh file `.sql` tĩnh đặt tại
  `infra/source-db/init/seed_data_amazon.sql` — Postgres tự chạy file này
  cùng `seed_data.sql` ngay lần đầu volume `source_db_data` được tạo, nên
  `make up` trên volume rỗng (máy mới, hoặc sau `make clean`) đã có sẵn data
  thật mà không cần chạy thêm lệnh nào. Đã kiểm chứng bằng container
  Postgres tạm mount `infra/source-db/init/` — 2 file `.sql` chạy tuần tự,
  tổng 1565 products/1573 users/16230 interactions, không có bản ghi mồ côi.
  Việc còn để ngỏ: `test.parquet`/`incoming/batch_*.parquet` (cùng bộ dữ
  liệu, xem `amazone_data/DATA.md`) chưa được nạp — dành để mô phỏng "batch
  mới về" khi cần demo drift ở Phase 4/7, chưa cần thiết cho seed ban đầu.

## 7. Danh sách file đã triển khai

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/extract/watermark.py` | Cursor kép `(updated_at, primary_key)`; đọc/ghi watermark + audit run trong Postgres | Bảng `pipeline.extraction_watermarks`, `pipeline.extraction_runs` (schema ở `infra/source-db/init/schema.sql`); `extraction_runs.batches` là JSONB **list**, append-only qua `commit_batch`/`fail_run` (sửa 2026-09-13, xem mục 4) |
| `jobs/extract/dlt_writer.py` | Gọi `dlt` load 1 batch retry-safe (`write_disposition="replace"`, dataset đặt tên theo hash cursor) | Từ 2026-09-06 (ADR 0003): nhận `bucket_url`/`credentials` S3, ghi thẳng MinIO, không còn tham số `output_root: Path` |
| `jobs/extract/run.py` | Entrypoint: lặp `fetch_batch → load qua dlt → commit_batch` cho từng nguồn | Đọc env `SOURCE_DB_DSN`, `EXTRACT_S3_BUCKET/ENDPOINT/ACCESS_KEY/SECRET_KEY/REGION`, `DLT_PIPELINES_DIR`, `EXTRACT_BATCH_SIZE`, `EXTRACT_FAIL_AFTER_WRITE` |
| `jobs/extract/sources/products.py` | Khai báo `SourceSpec` cho bảng `products` | `primary_key="product_id"`, kiểu `text` |
| `jobs/extract/sources/interactions.py` | Khai báo `SourceSpec` cho bảng `interactions` | `primary_key="interaction_id"`, kiểu `bigint` |
| `infra/docker/extract/Dockerfile` | Image job extract | `ENTRYPOINT ["python", "-m", "jobs.extract.run"]` |
| `requirements/extract.txt` | Dependency riêng cho image extract | `psycopg[binary]`, `dlt[filesystem,parquet]`, `s3fs` (thêm ở ADR 0003) |
| `scripts/run_job_locally.sh` | Chạy job ngoài Airflow (`bash scripts/run_job_locally.sh extract`) | Build + `docker compose run` cho `extract-job` |
| `scripts/simulate_incremental_update.py` | Seed thêm product/interaction mới để demo watermark | CLI: `--products` (mặc định 20), `--interactions` (mặc định 100), `--seed` |
| `scripts/seed_source_db.py` | Nạp `amazone_data/data_split/train.parquet` + `meta.parquet` (Amazon thật) vào `products`/`users`/`interactions`; hoặc sinh file `.sql` tĩnh qua `--dump-sql` | CLI: `--data-dir`, `--dump-sql`; đọc `SOURCE_DB_DSN`; idempotent qua `ON CONFLICT DO NOTHING` + dedupe `(user_id, product_id)` |
| `infra/source-db/init/seed_data_amazon.sql` | Snapshot SQL tĩnh (auto-generated, ~3.5MB) — Postgres tự chạy cùng `seed_data.sql` khi volume rỗng | Sinh bởi `seed_source_db.py --dump-sql`; **không sửa tay**, sinh lại khi đổi mapping/dữ liệu nguồn |
| `tests/unit/extract/test_watermark.py` | Unit test cursor + batch identity + fault injection | Chạy bằng `make test` |
| `docker-compose.yml` (service `extract-job`) | Chạy job extract qua Compose profile `jobs` | `profiles: [jobs]`; volume chỉ còn `./data/dlt-pipelines` (working dir của `dlt`, không phải nơi dữ liệu đi qua) |

> Bảng trên là lịch sử (kiến trúc watermark tự viết). Xem mục 8 cho danh
> sách file hiện tại.

## 8. Refactor sang dlt-native incremental (2026-09-27)

Thay thế toàn bộ cơ chế ở mục 2.1/2.2/2.3 (watermark + audit run tự viết
trong Postgres) bằng `dlt.sources.incremental` + pipeline state của chính
`dlt` — xem lý do đầy đủ và bug dẫn tới quyết định này trong
[ADR 0006](../decisions/0006-dlt-native-incremental-extract.md).

**Cơ chế mới**:

- `SourceSpec` (đổi từ `watermark.py` sang `jobs/extract/source_spec.py`)
  chỉ còn khai báo tên bảng/khoá chính — không còn cursor/run bookkeeping.
- `jobs/extract/dlt_writer.py::make_source_resource()` — dựng 1 dlt
  resource/nguồn dùng `dlt.sources.incremental("updated_at",
  initial_value=EPOCH)`. **Đã thử** thêm `primary_key=(source.primary_key,)`
  để dlt tự dedup dòng trùng boundary `updated_at` giữa các lần chạy khác
  nhau (kỳ vọng thay cursor kép cũ) — **thực nghiệm không đáng tin**: chạy
  extract 2 lần liên tiếp không đổi gì ở nguồn, lần 2 vẫn lấy lại gần như
  toàn bộ dữ liệu trùng boundary thay vì 0 dòng. Đã bỏ `primary_key` khỏi
  `incremental()`, dùng **strict `>`** cho trang đầu của mỗi lần chạy — an
  toàn vì phân trang trong cùng 1 lần chạy luôn lấy hết mọi dòng ở một
  boundary trước khi run kết thúc (chi tiết + đánh đổi: xem
  [ADR 0006](../decisions/0006-dlt-native-incremental-extract.md)).
  Phân trang **trong cùng 1 lần chạy** dùng row-comparison
  `(updated_at, pk) > (...)` (không có tranh chấp vì `pk` unique).
- `pipeline.run(resource, loader_file_format="parquet")` — bắt buộc chỉ định
  tường minh, dlt mặc định ghi `.jsonl.gz` nếu không có tham số này (phát
  hiện khi kiểm chứng thật, không phải giả định ban đầu).
- `jobs/extract/run.py` — 1 `dlt.pipeline()` bền vững/nguồn (khác hẳn thiết
  kế cũ: 1 pipeline/batch), `pipeline_name=f"reco_extract_{source.name}"`,
  `dataset_name="staging"`, `restore_from_destination=True` — trạng thái
  khôi phục từ chính MinIO (`extract-staging`), không phải Postgres.
- **Đảm bảo chống mất dữ liệu**: nếu resource generator raise giữa chừng
  (mô phỏng bằng `EXTRACT_FAIL_AFTER_ROWS=<source>:<n>`), bước `extract` của
  dlt fail **trước khi** `normalize`/`load` chạy — không có gì mới lên
  MinIO, và `updated_at.last_value` của lần chạy sau không đổi (vẫn là điểm
  dừng của lần **thành công** gần nhất). Đây chính là bất biến mà thiết kế
  cũ không đảm bảo được (watermark tiến theo batch, độc lập với kết quả cả
  run).
- Đổi tên biến môi trường: `EXTRACT_BATCH_SIZE` → `EXTRACT_PAGE_SIZE` (chỉ
  còn là kích thước trang SQL nội bộ); `EXTRACT_FAIL_AFTER_WRITE` →
  `EXTRACT_FAIL_AFTER_ROWS`.
- **Không giữ bảng audit Postgres song song** (`pipeline.extraction_runs` bị
  xoá khỏi `schema.sql`) — lịch sử chạy nằm trong log JSON structured
  (`load_id`, số dòng/nguồn) in ra ở `run.py`.

**Kiểm chứng thật** (không mock, `docker compose down --volumes` → `up` từ
volume rỗng):

| Kịch bản | Kết quả thực tế |
|---|---|
| Cold start, chạy extract 1 lần | 1565/1565 products, 16230/16230 interactions trong 1 lần chạy/nguồn |
| `EXTRACT_FAIL_AFTER_ROWS=interactions:5000`, chạy từ cold start | Raise `PipelineStepFailed` ở `step=extract`; xác nhận qua `s3fs`: `staging/interactions` **không tồn tại** (0 object) dù generator đã fetch >5000 dòng trước khi raise; `staging/products` có đúng 1 file (products không bị injection, extract trước interactions trong vòng lặp nguồn) |
| Bỏ `EXTRACT_FAIL_AFTER_ROWS`, chạy lại | Lấy đủ toàn bộ 16230 dòng interactions trong 1 lần (vì lần trước không commit gì) |
| Chạy thêm 1 lần nữa, không đổi gì ở nguồn | **Trước khi bỏ `primary_key`**: bug thật — lấy lại 1560/1560 products, 16222/16222 interactions (trùng lặp gần toàn bộ, xem ADR 0006). **Sau khi sửa** (strict `>`): 0 dòng cả 2 nguồn — đúng kỳ vọng |
| `simulate_incremental_update.py --products 5 --interactions 30` rồi extract lại | Chỉ lấy đúng 5+30 dòng mới (`page_cursor_to` khớp id mới nhất) |
| `make_bronze`/`build_bronze.py` sau đó | `bronze.products`=1570/1570, `bronze.interactions`=16260/16260 (1565+5, 16230+30); rerun lần 2 → `rows_committed: 0` cả 2 bảng |

### 8.1 Definition of Done — đối chiếu (sau refactor)

| Tiêu chí (từ `de-xuat-trien-khai.md`) | Trạng thái |
|---|---|
| Chạy `run_job_locally.sh extract` 2 lần, dữ liệu nguồn đổi giữa 2 lần, ra 2 batch không trùng/không sót | ✅ — đảm bảo bởi `dlt.sources.incremental` + `primary_key` dedup tại boundary |
| Một run bị crash giữa chừng không được làm mất hoặc kẹt dữ liệu đã ghi | ✅ (mục tiêu của refactor 2026-09-27) — khác bug mục 4 (dữ liệu ghi xong nhưng run cha `failed` khiến Bronze không bao giờ nạp), nay: run fail = không gì mới tới đích, không có trạng thái lửng |

### 8.2 Danh sách file đã triển khai (hiện tại)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/extract/source_spec.py` | Khai báo `SourceSpec` (tên bảng, khoá chính, kiểu) | Thay `watermark.py`; không còn cursor/run bookkeeping |
| `jobs/extract/dlt_writer.py` | `make_source_resource()` — dlt resource incremental thật, phân trang SQL nội bộ | `dlt.sources.incremental("updated_at", primary_key=...)`; đọc `EXTRACT_FAIL_AFTER_ROWS` để inject lỗi |
| `jobs/extract/run.py` | Entrypoint: 1 `dlt.pipeline()` bền vững/nguồn, gọi `pipeline.run()` | Đọc env `SOURCE_DB_DSN`, `EXTRACT_S3_*`, `DLT_PIPELINES_DIR`, `EXTRACT_PAGE_SIZE`, `EXTRACT_FAIL_AFTER_ROWS`; `dataset_name="staging"` |
| `jobs/extract/sources/products.py`, `interactions.py` | Khai báo `SourceSpec` cho từng bảng | Import từ `jobs.extract.source_spec` |
| `infra/source-db/init/schema.sql` | Schema nguồn | Đã xoá `CREATE SCHEMA pipeline` + 2 bảng watermark/run |
| `tests/unit/extract/test_extract.py` | Unit test `parse_fail_after_rows` + `SourceSpec` | Thay `test_watermark.py` |
| `docs/decisions/0006-dlt-native-incremental-extract.md` | ADR ghi lại quyết định + bug dẫn tới refactor | — |
