# 0003 — Extract ghi thẳng lên MinIO thay vì Parquet cục bộ

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-09-06
- **Phase liên quan**: Phase 1 (extract), ảnh hưởng Phase 2 (`build_bronze.py`)

## Bối cảnh

`docs/de-xuat-trien-khai.md` mục Phase 1 nói rõ: *"Output tạm thời có thể
ghi ra file Parquet cục bộ — **chưa cần Iceberg**, vì mục tiêu phase này là
validate watermark độc lập với lakehouse."* Đây là lựa chọn có chủ đích để
cô lập rủi ro kỹ thuật (xem giải thích gốc trong
`docs/modules/phase-1-extract-watermark.md`).

Trên thực tế vận hành, việc này gây ra một lỗ hổng: `data/extract/` không
có cơ chế bảo toàn (retention) nào — chỉ là thư mục cục bộ trên máy host.
Khi thư mục dự án bị đổi tên (một thao tác không liên quan tới pipeline),
toàn bộ Parquet đã extract trước đó biến mất, nhưng
`pipeline.extraction_runs` trong Postgres vẫn ghi nhận các run đó là
"succeeded". `jobs/transform/build_bronze.py` phải thêm logic
`skipped_missing_output` để xử lý graceful tình huống này (xem
`docs/modules/phase-2-lakehouse.md` mục 2.5) — một triệu chứng cho thấy
staging cục bộ là điểm yếu thật, không chỉ lý thuyết.

## Quyết định

`jobs/extract/dlt_writer.py` và `jobs/extract/run.py` sửa để `dlt` ghi
thẳng vào bucket MinIO `EXTRACT_STAGING_BUCKET` (mặc định
`extract-staging`, bucket mới, tách biệt khỏi `iceberg-warehouse` và
`mlflow-artifacts`) thay vì thư mục `data/extract/` trên host. Cụ thể:

- `load_batch_with_dlt()` nhận `bucket_url` (`s3://extract-staging`) +
  `credentials` (access key/secret/endpoint MinIO) thay vì `output_root:
  Path`.
- `jobs/extract/run.py` đọc cấu hình S3 từ biến môi trường mới:
  `EXTRACT_S3_ENDPOINT`, `EXTRACT_S3_BUCKET`, `EXTRACT_S3_ACCESS_KEY`,
  `EXTRACT_S3_SECRET_KEY`, `EXTRACT_S3_REGION` (tái dùng credential MinIO
  đã có, `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`).
- `pipeline.extraction_runs.output_path` giờ lưu URI dạng
  `s3://extract-staging/<dataset_name>` thay vì đường dẫn cục bộ.
- `jobs/transform/build_bronze.py` đọc Parquet qua `s3fs.S3FileSystem` +
  `pyarrow.dataset.dataset(..., filesystem=fs)` thay vì đọc từ
  `Path` cục bộ; kiểm tra tồn tại bằng `fs.exists()` thay vì
  `Path.exists()`.
- `docker-compose.yml`: bỏ volume `./data/extract` khỏi cả `extract-job`
  và `transform-job` — hai job không còn chia sẻ filesystem cục bộ với
  nhau nữa, chỉ còn chia sẻ qua MinIO. `./data/dlt-pipelines` (working
  directory nội bộ của `dlt` — schema/state cache, không phải nơi dữ liệu
  đi qua) vẫn giữ nguyên trên đĩa cục bộ, vì đây không phải cơ chế trao đổi
  dữ liệu giữa job với job.
- `infra/minio/init-buckets.sh` tạo thêm bucket `extract-staging`.

## Lý do

- **Đúng nguyên tắc CLAUDE.md #6** ("không lưu trạng thái quan trọng trên
  filesystem cục bộ của container — mọi dữ liệu đi qua MinIO/Iceberg/...")
  ngay từ Phase 1, thay vì đợi tới Phase 2 mới đưa dữ liệu vào MinIO.
- **Loại bỏ hẳn lớp "staging cục bộ không có retention"** — nguyên nhân gốc
  của lỗi thực tế đã gặp (mục Bối cảnh). Không cần thêm logic xử lý
  "batch bị mất" ở `build_bronze.py` cho dữ liệu extract **mới** từ giờ trở
  đi (logic `skipped_missing_output` vẫn giữ lại vì các run lịch sử trước
  khi có quyết định này vẫn còn trong `pipeline.extraction_runs`, và để
  phòng ngừa hi hữu MinIO object bị xoá thủ công).
- **Không tăng thêm rủi ro kỹ thuật đáng kể**: `dlt`'s filesystem
  destination đã hỗ trợ S3-compatible storage sẵn (chỉ cần thêm
  `credentials` + `s3fs`), không phải tích hợp mới hoàn toàn — khác với
  PyIceberg/REST Catalog (rủi ro cao, lý do giữ Phase 2 tách riêng).
- **Bucket `extract-staging` tách riêng khỏi `iceberg-warehouse`**: dữ liệu
  ở đây vẫn chỉ là "Parquet thô từ dlt", chưa qua schema contract/validator
  — không được lẫn với dữ liệu Iceberg đã enforce schema, giữ đúng ranh
  giới "Bronze Iceberg mới là nguồn dữ liệu chính thức" đã nêu trong
  `phase-2-lakehouse.md`.

## Hệ quả

- `docs/de-xuat-trien-khai.md` Phase 1 DoD ("output tạm thời ghi ra file
  Parquet cục bộ") coi như đã **vượt qua có chủ đích** — output giờ đây bền
  vững hơn yêu cầu tối thiểu ban đầu. Không cần làm lại gì ở Phase 1.
- `jobs/extract/dlt_writer.py`, `jobs/extract/run.py`,
  `jobs/transform/build_bronze.py`, `docker-compose.yml`,
  `infra/minio/init-buckets.sh`, `.env.example`, `README.md` đều cập nhật
  theo quyết định này (xem diff tương ứng).
- Thêm dependency `s3fs` vào cả `requirements/extract.txt` và
  `requirements/transform.txt` (trước đó `s3fs` mới chỉ là dependency
  gián tiếp của `libs` qua `pyiceberg[s3fs]`).
- Không ảnh hưởng cơ chế idempotent đã có: `batch_identity()` +
  `write_disposition="replace"` hoạt động y hệt trên S3 như trên đĩa cục
  bộ — đã kiểm chứng lại bằng kịch bản `EXTRACT_FAIL_AFTER_WRITE`.

## Tham chiếu

`jobs/extract/dlt_writer.py`, `jobs/extract/run.py`,
`jobs/transform/build_bronze.py`, `docker-compose.yml` (service
`extract-job`, `transform-job`), `infra/minio/init-buckets.sh`;
`docs/modules/phase-1-extract-watermark.md`,
`docs/modules/phase-2-lakehouse.md` mục 2.5.
