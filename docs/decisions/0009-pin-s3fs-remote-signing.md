# 0009 — Ghim `s3fs`/`fsspec` 2026.1.0 để giữ remote signing của Lakekeeper

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-24
- **Phase liên quan**: Phase 2 (Lakehouse), ảnh hưởng mọi image đọc/ghi
  Iceberg (transform, monitoring, training; serving/materialize chỉ qua
  ràng buộc trong `libs`)

## Bối cảnh

Task `dag_transform.build_bronze` fail lúc commit `bronze.interactions`
(2026-11-24): `PermissionError: Forbidden` (HeadObject 403) khi PyIceberg đọc
manifest list của snapshot trước. Chạy tay `make bronze` ngay sau đó lại
thành công, nên đây là lỗi lúc có lúc không.

Bảng Iceberg dùng **remote signing** của Lakekeeper: PyIceberg 0.12.0 tạo
client s3fs với `signature_version=UNSIGNED` và gắn hook `before-sign.s3`
(`S3V4RestSigner`) vào `fs.s3.meta.events` của client đang có. Từ
**s3fs 2026.2.0**, `S3FileSystem.set_session()` âm thầm đặt `refresh=True`
và tạo client mới khi mọi aiohttp session của client cũ đã đóng. Client mới
không có hook, nên request đi dạng không ký, MinIO coi là anonymous và trả
403. Log Lakekeeper xác nhận điều này: không có request ký nào cho file bị
lỗi.

Đã tái hiện chắc chắn: đọc một lần, đóng aiohttp session của client, đọc lại
thì lỗi y hệt, và client mới không có hook ký. Kiểm tra mã nguồn wheel:
2025.9.0 → 2026.1.0 không có đoạn auto-refresh này; 2026.2.0, 2026.3.0,
2026.4.0, 2026.6.0, 2026.7.0 có. Trước khi sửa, image `training` resolve
s3fs 2026.9.0 nên cũng bị ảnh hưởng. `serving`/`materialize` bị `feast`
kéo về 2024.9.0 nên không bị.

## Quyết định

- `requirements/transform.txt`, `requirements/monitoring.txt`: ghim
  `s3fs==2026.1.0` và `fsspec==2026.1.0` (trước đó là 2026.7.0).
- `libs/pyproject.toml`: thêm `s3fs<2026.2.0` (chặn trên, không ghim cứng,
  để image có feast vẫn tự resolve bản 2024.9.0); `libs` lên 0.7.1.
- `requirements/extract.txt` **giữ nguyên** 2026.7.0: extract không dùng
  Iceberg/remote signing, ký bằng key tĩnh nên client có bị tạo lại cũng
  vẫn ký đúng. Giữ nguyên cũng tránh động vào ràng buộc fsspec của `dlt`.
- Thêm `tests/integration/test_remote_signing.py` chặn lỗi này quay lại.

## Lý do

- Sửa ở mức version thư viện, không monkeypatch s3fs/PyIceberg trong code
  của project.
- Các phương án khác đều lớn hơn: chuyển Lakekeeper sang vended credentials
  cần STS trên MinIO và đổi storage profile; tự gắn lại hook sau mỗi lần
  refresh thì phải dựa vào API nội bộ của s3fs.

## Hệ quả

- Không đổi checklist phase nào; không thêm ngoại lệ vào nguyên tắc 10.1.
- **Trước khi nâng s3fs/fsspec hoặc PyIceberg**, phải chạy
  `make test-integration`. Chỉ bỏ ghim khi test remote signing pass, tức là
  bản mới đã giữ được hook hoặc PyIceberg đã gắn hook theo cách sống sót qua
  refresh.
- Lần chạy lỗi để lại 1 data file + 1 manifest (`e78a2bf3…`) không được
  snapshot nào trỏ tới trong `bronze.interactions`; vô hại, có thể dọn bằng
  remove-orphan-files sau.

## Tham chiếu

`requirements/transform.txt`, `requirements/monitoring.txt`,
`libs/pyproject.toml`, `libs/CHANGELOG.md` (0.7.1),
`tests/integration/test_remote_signing.py`,
`libs/src/reco_mlops_libs/iceberg/catalog.py`,
`docs/modules/phase-2-lakehouse.md`.
