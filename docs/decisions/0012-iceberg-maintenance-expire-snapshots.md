# 0012 — Bảo trì Iceberg: compact Silver và `expire_snapshots` có xoá file

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-24
- **Phase liên quan**: Phase 7 (`dag_maintenance`), ảnh hưởng Phase 2

## Bối cảnh

Phase 7 chỉ compact Bronze và để lại `expire_snapshots` (`phase-7-monitoring.md`
mục 2.4 và "việc còn lại"). Hai thay đổi sau đó làm việc này cần xử lý:

- ADR 0010 biến Silver thành upsert tăng dần, mỗi lần chạy có dữ liệu mới thêm
  file nhỏ. Đo trên bảng test: 6 lần chạy tăng dần để lại 13 file dữ liệu
  sống (2 file mỗi lần).
- Mọi bảng đều tích snapshot: bảng thật hiện có Silver 13–14, Gold 5–9 snapshot,
  và mỗi snapshot cũ giữ file cũ trên MinIO.

Thiết kế gốc không nói gì về retention. Đây là việc **mới**, không lệch tài liệu
gốc, nhưng kết quả đo (dưới đây) khác với giả định "gọi `expire_snapshots` là đủ"
nên ghi lại ở ADR.

## Quyết định

1. `jobs/maintenance/compact_iceberg.py` compact thêm `silver.products` và
   `silver.interactions` (cùng cách overwrite chính nó). Gold không compact vì
   đã `overwrite()` toàn bộ mỗi lần. `overwrite` không đụng table properties nên
   watermark Silver (`reco.silver.watermark_ingested_at`) còn nguyên — có test.
2. Job mới `jobs/maintenance/expire_snapshots.py`, áp cho 6 bảng Bronze/Silver/
   Gold, **tự xoá file** sau khi commit expire:
   - chọn snapshot cũ hơn `--older-than-days` (mặc định 7, hoặc env
     `EXPIRE_OLDER_THAN_DAYS`), không bao giờ động tới snapshot hiện tại hay
     đầu nhánh/tag;
   - trước khi commit, tính tập file (manifest list, manifest, data file) của
     snapshot bị expire trừ tập file của mọi snapshot còn giữ; chỉ xoá phần hiệu;
   - commit expire trước, xoá file sau: nếu sập giữa chừng chỉ để lại file mồ
     côi, không bao giờ để bảng trỏ tới file đã mất;
   - lỗi xoá một file được báo (`files_failed`) và đi tiếp;
   - `--dry-run` chỉ báo cáo.
3. `dag_maintenance`: `compact_iceberg >> expire_snapshots`.

## Lý do

Hai kết quả đo trên `pyiceberg==0.12.0` + Lakekeeper + MinIO quyết định thiết kế:

- **`expire_snapshots()` của PyIceberg chỉ gỡ snapshot khỏi metadata, không xoá
  file.** Sau khi expire 20 snapshot, cả 20 file dữ liệu vẫn nằm trong MinIO;
  Lakekeeper cũng không dọn. Chỉ gọi hàm đó sẽ không giải phóng byte nào, nên
  job tự xoá phần file mà chỉ snapshot bị expire dùng.
- **`table.io.delete()` không dùng được**: s3fs gộp thành `POST /<bucket>?delete`
  và bộ ký của Lakekeeper trả `SignError 400`. `fs.rm_file()` gửi `DELETE
  /<bucket>/<key>` đơn lẻ, được ký bình thường.

Phương án đã loại:

- **Chỉ expire metadata, để file mồ côi**: không đạt mục tiêu.
- **Xoá mọi file mà snapshot bị expire liệt kê**: sai khi không compact, vì
  snapshot hiện tại dùng chung file dữ liệu với snapshot cũ. Có test
  (`shared-files`) và kiểm tra đột biến: bỏ phép trừ làm test fail.
- **Công cụ ngoài (Spark/`rewrite_data_files`)**: thêm service chỉ để thay một
  thao tác; xem ADR 0010.

## Hệ quả

- **Mất time travel và khả năng tái lập**: huấn luyện log
  `iceberg_snapshot_id.<bảng>` vào MLflow. Khi snapshot đó bị expire, không đọc
  lại đúng dữ liệu huấn luyện của model cũ được. Mặc định 7 ngày; tăng
  `EXPIRE_OLDER_THAN_DAYS` nếu cần giữ lâu hơn.
- **Không chạy chồng** với `dag_transform`/`dag_ingest` (cả compact lẫn expire
  ghi lại bảng); hiện các DAG chạy tay `schedule=None`.
- Không tự dọn file mồ côi có từ trước (không thuộc snapshot nào đã expire) và
  không xoá file `metadata.json` cũ.
- Test `tests/integration/test_maintenance.py` (6 test) chạy trên bảng
  `*.it_test_mt_*`. Dò tồn tại file đã xoá phải bắt `SignError` vì s3fs rơi về
  liệt kê prefix khi HEAD trả 404 (ghi ở docstring test).
- Chưa chạy xoá thật trên bảng thật: với mặc định 7 ngày chưa snapshot nào đủ
  cũ (dữ liệu từ 2026-11-19). `--dry-run --older-than-days 0` cho thấy sẽ gỡ 40
  snapshot và xoá 96 file.

## Tham chiếu

`jobs/maintenance/compact_iceberg.py`, `jobs/maintenance/expire_snapshots.py`,
`airflow/dags/dag_maintenance.py`, `tests/integration/test_maintenance.py`,
`docs/modules/phase-7-monitoring.md` (mục 2.4), `docs/decisions/0010-silver-incremental.md`.
