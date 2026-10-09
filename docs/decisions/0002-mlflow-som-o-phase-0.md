# 0002 — Dựng hạ tầng MLflow ngay ở Phase 0 thay vì Phase 4

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-08 (Phase 0)
- **Phase liên quan**: Phase 0 (hạ tầng nền), ảnh hưởng Phase 4 (Training &
  Model Registry)

## Bối cảnh

`docs/de-xuat-trien-khai.md` đặt việc dựng `infra/mlflow/
docker-compose.mlflow.yml` + `mlflow.env` vào checklist Phase 4, với lý do
thứ tự phase là "hạ tầng nền trước, business logic sau" — nhưng MLflow
tracking server tự nó cũng là hạ tầng, không phải business logic.

## Quyết định

Dựng service `mlflow` (tracking server) và `mlflow-db` (Postgres backend
store riêng) ngay trong `docker-compose.yml` gốc ở Phase 0, thay vì tách
thành file compose riêng ở Phase 4 như kế hoạch ban đầu.

## Lý do

- MLflow tracking server không phụ thuộc bất kỳ dữ liệu nghiệp vụ nào
  (source-db, Iceberg) — dựng sớm không vi phạm nguyên tắc "hạ tầng nền
  trước, business logic sau", vì đây vẫn thuộc nhóm hạ tầng nền.
- Dựng sớm giúp Phase 4 (Training) chỉ cần tập trung viết
  `train_ranker.py`/`evaluate.py`/`promote.py` mà không phải đồng thời dựng
  và debug hạ tầng — giảm số biến thay đổi cùng lúc ở Phase 4.
- `mlflow-db` được tách biệt hoàn toàn khỏi `source-db` và `catalog-db` từ
  đầu, đúng nguyên tắc "không lẫn dữ liệu nghiệp vụ giả lập với metadata
  MLOps" đã nêu trong `bao-cao-ky-thuat.md` mục 7.2 — dựng sớm không thay
  đổi nguyên tắc này, chỉ thay đổi thời điểm.

## Hệ quả

- `docs/de-xuat-trien-khai.md` checklist Phase 4 mục "`infra/mlflow/
  docker-compose.mlflow.yml` + `mlflow.env`" coi như **đã hoàn thành từ
  Phase 0** — khi thực hiện Phase 4, bỏ qua mục này trong checklist gốc,
  không dựng lại.
- Không có file `docker-compose.mlflow.yml` riêng như tên gọi trong kế
  hoạch gốc — service `mlflow`/`mlflow-db` nằm trực tiếp trong
  `docker-compose.yml` chính. Nếu cần tách ra sau (ví dụ để bật/tắt độc
  lập), cần một ADR mới.
- Không ảnh hưởng phần business logic của Phase 4 (`build_training_set.py`,
  `train_ranker.py`, `registry_client.py`, `promote.py`) — các file này vẫn
  cần viết mới ở Phase 4 như kế hoạch.

## Tham chiếu

`docker-compose.yml` (service `mlflow`,
`mlflow-db`); `docs/modules/phase-0-ha-tang-nen.md` mục "Khác biệt so với đề
xuất ban đầu".
