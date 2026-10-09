# 0006 — dlt sở hữu cursor + state của extract, thay watermark tự viết trong Postgres

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-09-27
- **Phase liên quan**: Phase 1 (extract), ảnh hưởng Phase 2 (`build_bronze.py`)

## Bối cảnh

`de-xuat-trien-khai.md` mục Phase 1 ghi: *"đọc/ghi `last_watermark` bền
vững (ví dụ lưu trong một bảng Postgres riêng, không lưu trong container)"*
— đây là lựa chọn có chủ đích từ đầu, không phải sơ suất, và đã triển khai
đúng như vậy (`jobs/extract/watermark.py`, xem
`docs/modules/phase-1-extract-watermark.md`).

Khi test thủ công lại pipeline Phase 0→3 (2026-09-27, trước khi bắt đầu
Phase 4), phát hiện bug mất dữ liệu thật: `extract-job` bị crash giữa chừng
(lỗi filesystem khi bind-mount `data/dlt-pipelines` trên Windows/Docker
Desktop). `commit_batch()` đã tiến watermark ngay sau mỗi batch nội bộ ghi
thành công lên MinIO, độc lập với kết quả cuối cùng của cả run — nhưng
`jobs/transform/build_bronze.py::fetch_pending_runs()` chỉ đọc run có
`status = 'succeeded'` trong `pipeline.extraction_runs`. Run bị crash có
`status = 'failed'`, dù đã ghi thành công 14 batch nội bộ (~12.992/16.230
dòng `interactions`) lên MinIO. Kết quả: watermark đã đi qua các dòng đó
(sẽ không bao giờ extract lại), nhưng `build_bronze.py` không bao giờ nạp
chúng vào Bronze vì run cha không đạt `succeeded` — mất dữ liệu âm thầm,
không exception, không cảnh báo.

Gốc rễ: hai tầng bookkeeping tự viết (watermark tiến theo *batch*, gating
Bronze theo *trạng thái cả run*) không nhất quán về granularity. Đây đúng
là lớp lỗi mà `dlt` — đã là dependency có sẵn từ Phase 1, nhưng trước đó chỉ
dùng làm writer Parquet — vốn giải quyết sẵn qua `dlt.sources.incremental`
kết hợp pipeline state: cursor chỉ tiến khi TOÀN BỘ `pipeline.run()`
(extract → normalize → load) thành công, atomic theo từng lần chạy.

## Quyết định

Bỏ hẳn watermark/audit tự viết trong Postgres, để `dlt` sở hữu cursor và
trạng thái extract:

- `jobs/extract/watermark.py` → đổi tên `jobs/extract/source_spec.py`, chỉ
  còn giữ `SourceSpec`. Xoá toàn bộ `Cursor`, `ensure_metadata_tables`,
  `load_cursor`, `build_incremental_query`, `commit_batch`, `start_run`,
  `finish_run`, `fail_run`.
- `jobs/extract/dlt_writer.py::make_source_resource()` — resource dlt thật,
  dùng `dlt.sources.incremental("updated_at", initial_value=EPOCH)`.
  **Đã thử** thêm `primary_key=(source.primary_key,)` trên `incremental()`
  để dlt tự dedup dòng trùng boundary `updated_at` giữa các lần chạy khác
  nhau (kỳ vọng thay thế đúng vai trò cursor kép cũ) — **thực nghiệm cho
  thấy không đáng tin**: chạy extract 2 lần liên tiếp không đổi gì ở nguồn,
  lần 2 vẫn trích xuất lại gần như toàn bộ dữ liệu trùng boundary (1560/1565
  products, 16222/16230 interactions) thay vì 0 dòng — dlt phục hồi đúng
  scalar `last_value` từ MinIO nhưng không phục hồi đáng tin cậy tập
  "primary key đã thấy tại boundary" giữa các lần `pipeline.run()` (với dlt
  1.30.0 + `filesystem` destination). Đã bỏ `primary_key` khỏi
  `incremental()`, đổi trang đầu tiên của mỗi lần chạy sang **strict `>`**
  trên `updated_at` (không `>=`). An toàn vì phân trang **trong cùng 1 lần
  chạy** luôn lấy hết mọi dòng ở một boundary trước khi run đó kết thúc
  (vòng lặp chỉ dừng khi trang rỗng/ngắn hơn `page_size`) — nên lần chạy
  sau chỉ cần bỏ qua đúng những gì `<=` giá trị cuối của lần chạy **thành
  công** gần nhất, không cần dlt dedup theo primary key nữa. Đánh đổi: một
  dòng mới có `updated_at` trùng khớp chính xác một giá trị đã xử lý xong
  sẽ bị bỏ sót — chỉ xảy ra với dữ liệu seed hàng loạt dùng chung 1
  timestamp (như dataset Amazon), không ảnh hưởng ghi tăng dần thực tế (mỗi
  dòng mới có `updated_at` riêng).
- `pipeline.run(resource, loader_file_format="parquet")` — phải chỉ định
  tường minh, nếu không dlt mặc định ghi `.jsonl.gz` (phát hiện khi kiểm
  chứng: `build_bronze.py` đọc Parquet nên im lặng bỏ qua nguồn không tồn
  tại nếu thiếu tham số này).
- `jobs/extract/run.py` — 1 `dlt.pipeline()` bền vững/nguồn (không còn 1
  pipeline/batch như cũ), `dataset_name="staging"`,
  `restore_from_destination=True` — trạng thái được khôi phục từ chính
  MinIO (`extract-staging`), không phải từ Postgres.
- `jobs/transform/build_bronze.py` — bỏ hẳn tham số Postgres. Đọc toàn bộ
  dataset staging của 1 nguồn qua `pyarrow`/`s3fs`, lọc theo `_dlt_load_id`
  (dlt tự thêm vào mọi dòng) đã có trong Bronze (`committed_run_ids()`,
  không đổi code, chỉ đổi ý nghĩa giá trị). `_dlt_load_id` được stamp thành
  `_extraction_run_id` — **giữ nguyên tên cột Iceberg**, không đổi schema
  contract trong `libs/`, không cần bump version.
- `infra/source-db/init/schema.sql` — xoá bảng
  `pipeline.extraction_watermarks`, `pipeline.extraction_runs`.
- `requirements/transform.txt` — bỏ `psycopg[binary]` (chỉ
  `build_bronze.py` dùng); `docker-compose.yml` service `transform-job` bỏ
  env `SOURCE_DB_DSN` và phụ thuộc `source-db` — transform-job hết nhu cầu
  truy cập Postgres.
- Đổi tên biến môi trường cho khớp ngữ nghĩa mới: `EXTRACT_BATCH_SIZE` →
  `EXTRACT_PAGE_SIZE` (chỉ còn là kích thước trang SQL nội bộ, không phải
  đơn vị audit); `EXTRACT_FAIL_AFTER_WRITE` → `EXTRACT_FAIL_AFTER_ROWS`
  (`<source>:<n>`, raise ngay trong resource generator sau khi yield đủ N
  dòng — mô phỏng crash giữa chừng để kiểm chứng cơ chế mới).

**Không giữ lại bảng audit song song** (đã cân nhắc, quyết định bỏ hẳn):
lịch sử chạy nằm trong log JSON structured đã in ra (`load_id`, số dòng/
nguồn) + metadata nội tại của dlt (`_dlt_loads`, v.v.) nằm ngay trong MinIO
cùng dữ liệu — không cần một tầng bookkeeping song song có thể lệch pha với
chính nó, đúng nguyên nhân gốc của bug này.

## Lý do

- Giải quyết tận gốc lớp lỗi "hai tầng bookkeeping lệch granularity", không
  chỉ vá triệu chứng cụ thể vừa gặp.
- Đúng CLAUDE.md nguyên tắc #6 (giảm state phụ ngoài MinIO/Iceberg) và giảm
  coupling giữa `transform-job` và Postgres (không tầng nào cần biết về
  tầng khác ngoài qua MinIO/Iceberg, gần hơn với nguyên tắc #3).
- `dlt` vốn đã là dependency của Phase 1 (dùng làm writer) — dùng đúng khả
  năng sẵn có của nó thay vì viết lại một phần việc nó đã làm tốt, thay vì
  thêm thư viện/hạ tầng mới.
- Đánh đổi đã cân nhắc: mất đi bảng Postgres có thể `SELECT` trực tiếp để
  tra lịch sử chạy (tiện khi trình bày bảo vệ khoá luận) — bù lại bằng log
  JSON structured đã có sẵn theo quy ước project.

## Hệ quả

- **Lệch khỏi `de-xuat-trien-khai.md` Phase 1** (đã trích ở mục Bối cảnh) —
  không còn lưu watermark trong "một bảng Postgres riêng". ADR này là văn
  bản hoá cho độ lệch đó.
- `docs/modules/phase-1-extract-watermark.md` — thêm mục mới mô tả refactor
  này, giữ nguyên nội dung cũ (mục 2.1-2.3, mục 4) làm lịch sử vì vẫn đúng
  về mặt đã từng xảy ra, chỉ đánh dấu "đã thay thế".
- `docs/modules/phase-2-lakehouse.md` — cập nhật đoạn mô tả cơ chế
  idempotent của `build_bronze.py`.
- Cần `docker compose down --volumes` (volume rỗng) để `schema.sql` mới áp
  dụng — init script Postgres chỉ chạy khi volume trống.
- Không ảnh hưởng `libs/` — không cần bump version/`libs/CHANGELOG.md`.

## Tham chiếu

`jobs/extract/source_spec.py`, `jobs/extract/dlt_writer.py`,
`jobs/extract/run.py`, `jobs/transform/build_bronze.py`,
`jobs/transform/iceberg_writer.py::committed_run_ids`,
`infra/source-db/init/schema.sql`, `docker-compose.yml` (service
`extract-job`, `transform-job`); `docs/modules/phase-1-extract-watermark.md`,
`docs/modules/phase-2-lakehouse.md`.
