# 0010 — Silver tăng dần (watermark `_ingested_at` + upsert) thay cho tính lại toàn bộ

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-24
- **Phase liên quan**: Phase 2 (Lakehouse), ảnh hưởng Phase 6 (`dag_transform`)

## Bối cảnh

Thiết kế Phase 2 (`docs/modules/phase-2-lakehouse.md` mục 2.6 và mục 6) chủ
ý cho `build_silver` **đọc toàn bộ Bronze, dedup bằng SQL rồi `overwrite()`
toàn bộ Silver** mỗi lần chạy — chấp nhận được ở quy mô MVP. Hai hệ quả đáng
sửa khi dữ liệu lớn dần:

- chi phí tỷ lệ với kích thước Bronze, không với lượng dữ liệu mới;
- mỗi lần chạy tạo snapshot mới ghi lại toàn bộ file dữ liệu, nên MinIO tích
  tụ bản sao (`compact_iceberg` chưa `expire_snapshots`).

Đây là chỗ **lệch** so với thiết kế gốc nên cần ADR (CLAUDE.md, quy ước #3).

## Quyết định

`build_silver` chỉ xử lý phần Bronze nạp sau lần chạy thành công trước, rồi
upsert vào `silver.products` (khoá `product_id`) và `silver.interactions`
(khoá `interaction_id`):

1. **Watermark** = `max(_ingested_at)` của các dòng Bronze đã xử lý, lưu trong
   **table properties của chính bảng Silver**, khoá
   `reco.silver.watermark_ingested_at` (ISO-8601 UTC, micro giây). Iceberg vẫn
   là nguồn sự thật duy nhất (nguyên tắc #3, #6), không có bảng phụ.
2. Đọc delta bằng `bronze_table.scan(row_filter="_ingested_at > '<watermark>'")`,
   khử trùng bằng **đúng SQL cũ** (`sql/silver/*.sql` không đổi) đăng ký dưới
   tên view cũ.
3. **Loại dòng cũ hơn bản đã có**: bỏ các dòng delta có `updated_at` ≤
   `updated_at` của Silver cùng khoá. Bắt buộc vì `Table.upsert` của PyIceberg
   ghi đè mọi dòng khớp mà có cột khác biệt, không tự so `updated_at`.
4. Validate pandera trên delta đã lọc, rồi trong **một transaction**:
   `tx.upsert(df, join_cols=[khoá])` + `tx.set_properties({watermark})`. Dữ
   liệu và watermark nằm cùng một snapshot nên không có bước "sau commit" nào
   có thể hỏng và để hai thứ lệch nhau. `Transaction.upsert`/`set_properties`
   có sẵn trong PyIceberg 0.12.0 (đã kiểm tra).
5. Không có dòng mới → không commit gì (không snapshot rỗng). Delta chỉ gồm
   dòng cũ hơn → chỉ cập nhật watermark (commit thuộc tính, không có snapshot
   dữ liệu).
6. **Chưa có watermark** (lần đầu, hoặc Silver có từ trước khi có cơ chế này)
   hoặc có cờ **`--full-refresh`** → chế độ `full`: đọc toàn bộ Bronze,
   `overwrite` Silver rồi đặt watermark, trong một transaction.

Kết quả mỗi bảng thêm các trường `mode`, `bronze_rows_scanned`,
`rows_inserted`, `rows_updated`, `rows_upserted`, `rows_skipped_stale`,
`watermark_before`, `watermark_after` (giữ `table`, `rows_after_dedup` = số
dòng thực sự đưa vào Silver). Ở chế độ full, mọi dòng tính là `rows_inserted`.

Code nằm trong `jobs/transform/` (`build_silver.py`, `iceberg_writer.py`),
**không** sửa `libs/` nên không đổi version `libs`. Không đổi schema contract,
SQL, DAG; Gold vẫn tính lại toàn bộ từ Silver.

## Lý do

Các phương án đã loại:

- **Bảng watermark phụ** (Postgres hoặc Iceberg): hai nơi giữ trạng thái có
  thể lệch nhau khi crash giữa hai lần ghi; thuộc tính bảng đi cùng snapshot.
- **Dùng `_extraction_run_id` làm mốc**: là dlt load id, không đảm bảo so sánh
  được theo thứ tự và đã dùng cho idempotency của Bronze (ADR 0006).
- **Giữ `overwrite` toàn bộ**: đúng nhưng không giải quyết hai vấn đề ở trên.
- **Spark/dbt/MERGE engine khác**: thêm service vào stack chỉ để thay một
  upsert; PyIceberg 0.12 đã đủ cho quy mô này.

## Hệ quả

- **Tương đương với full-refresh** là bất biến chính. Kiểm chứng bằng
  `tests/integration/test_silver_incremental.py` (có test so Silver sau chuỗi
  nhiều lần nạp với `--full-refresh` trên cùng Bronze) và trên dữ liệu thật:
  digest nội dung `silver.products` (687 dòng) và `silver.interactions`
  (14.769 dòng) giống hệt trước và sau `--full-refresh`. `upsert` của PyIceberg
  còn mới, nên đừng bỏ test này.
- **Phải chạy `--full-refresh`** khi đổi schema/SQL Silver, nghi ngờ lệch dữ
  liệu, hoặc gặp `ValueError: Target table has duplicate rows` từ upsert (lỗi
  được để nổi lên, không nuốt). Ghi vào `docs/runbook.md`.
- **Giả định về `_ingested_at`**: `build_bronze` gán một `_ingested_at` cho cả
  lần chạy ngay trước khi commit. Cơ chế đúng khi các lần `build_bronze` không
  chạy chồng nhau (hiện `dag_ingest` rồi `dag_transform` chạy tuần tự). Nếu hai
  lần chạy chồng nhau, lần commit sau mang `_ingested_at` nhỏ hơn có thể nằm
  dưới watermark và bị bỏ sót cho tới lần `--full-refresh` kế tiếp.
- Dòng cùng `updated_at` với bản đã có bị coi là cũ (bỏ qua), khớp quy ước
  của SQL dedup hiện tại.
- Chế độ full vẫn tạo 2 snapshot (xoá + ghi). Snapshot cũ vẫn tích tụ cho tới
  khi có `expire_snapshots` — việc độc lập, chưa làm.
- Không có checklist phase nào bị ảnh hưởng; không có ngoại lệ mới cho nguyên
  tắc 10.1.
- Chưa kiểm chứng việc partition Bronze theo `_ingested_at` có giúp `row_filter`
  loại file hay không (Bronze hiện không partition); tách thành việc riêng.

## Tham chiếu

`jobs/transform/build_silver.py`, `jobs/transform/iceberg_writer.py`
(`read_watermark`, `upsert_with_watermark`, `overwrite_with_watermark`),
`tests/integration/test_silver_incremental.py`,
`docs/modules/phase-2-lakehouse.md` (mục 2.4, 2.6, 2.9, 6),
`docs/decisions/0006-dlt-native-incremental-extract.md`,
`side_doc/SPEC-silver-incremental.md`.
