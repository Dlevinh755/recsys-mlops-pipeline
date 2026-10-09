# 0001 — Dùng Lakekeeper thay vì tự cấu hình REST Catalog

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-08 (Phase 0)
- **Phase liên quan**: Phase 0 (hạ tầng nền)

## Bối cảnh

`docs/cau-truc-project.md` và `docs/de-xuat-trien-khai.md` (Phase 0) mô tả
`infra/iceberg-catalog/catalog.env` như một cấu hình REST Catalog dùng
chung, không chỉ rõ implementation cụ thể. Cần một REST Catalog server thật
để PyIceberg (Phase 2) commit bảng vào — đây được đánh giá là phần rủi ro kỹ
thuật cao nhất của dự án.

## Quyết định

Dùng **Lakekeeper** (`quay.io/lakekeeper/catalog`) làm REST Catalog server,
thay vì tự viết/tự cấu hình một REST Catalog tối giản. Cụ thể:

- Thêm service `catalog-db` (Postgres riêng) làm backend store cho
  Lakekeeper.
- Service `catalog-migrate` chạy `lakekeeper migrate` trước khi
  `lakekeeper serve` khởi động.
- Service `catalog-bootstrap` gọi API Lakekeeper qua `curl` để tạo warehouse
  `reco` trỏ vào bucket MinIO `iceberg-warehouse`.
- `infra/iceberg-catalog/catalog.env` vẫn giữ nguyên vai trò file cấu hình
  môi trường, nhưng nội dung là biến cấu hình riêng của Lakekeeper.

## Lý do

- Lakekeeper là implementation REST Catalog trưởng thành, tuân thủ Iceberg
  REST Catalog spec, có sẵn healthcheck, migration tool, và API quản trị
  warehouse — giảm đáng kể rủi ro so với tự viết catalog tối giản chỉ đủ
  dùng cho MVP.
- Vì Phase 2 (PyIceberg commit) đã được xác định là rủi ro kỹ thuật cao
  nhất, nên ưu tiên giảm rủi ro ở tầng catalog ngay từ Phase 0, tránh vừa
  debug PyIceberg vừa debug catalog tự viết cùng lúc.
- Chi phí thêm: 1 Postgres service + 2 job một lần (`catalog-migrate`,
  `catalog-bootstrap`). Chi phí vận hành thấp, chấp nhận được cho một hệ
  chạy local.

## Hệ quả

- Thêm dependency vào một dự án mã nguồn mở bên thứ ba (Lakekeeper) thay vì
  code tự viết — cần theo dõi version compatibility với PyIceberg ở Phase 2.
- `libs/src/reco_mlops_libs/iceberg/catalog.py` (Phase 2) sẽ kết nối tới
  Lakekeeper qua REST Catalog client chuẩn của PyIceberg — không cần biết
  Lakekeeper là implementation cụ thể, đúng nguyên tắc tách compute khỏi
  storage đã nêu trong `bao-cao-ky-thuat.md` mục 3.
- Không ảnh hưởng các phase sau — mọi engine đọc Iceberg (DuckDB, Spark sau
  này) vẫn nói chuyện qua REST Catalog protocol chuẩn.

## Tham chiếu

`docker-compose.yml` (service
`catalog-db`, `catalog-migrate`, `lakekeeper`, `catalog-bootstrap`);
`infra/iceberg-catalog/bootstrap.sh`, `catalog.env`.
