# 0007 — Hạ `feast` từ 0.66.0 xuống 0.55.0 để tương thích với `mlflow`

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-09-27
- **Phase liên quan**: Phase 5 (Serving), ảnh hưởng Phase 3 (Feature Store —
  đổi version dependency dùng chung với `materialize-job`)

## Bối cảnh

Phase 5 cần một service (`serving`) dùng **đồng thời** `feast` (đọc online
feature từ Redis) và `mlflow` (poll registry, load model production) —
lần đầu tiên 2 dependency này phải cùng tồn tại trong 1 image. Khi build,
`pip` báo xung đột không thể giải:

```
mlflow-skinny 3.12.0 depends on starlette<1
feast 0.66.0 depends on starlette>=1.0.1
```

Đây là xung đột **cứng**, không phải lỗi ghim version tuỳ tiện — hai
package yêu cầu 2 nhánh version `starlette` không giao nhau. Đã thử loại
`prometheus-fastapi-instrumentator` (cũng ghim `starlette<1.0.0`, cùng họ
xung đột) nhưng vấn đề gốc vẫn còn vì `mlflow-skinny` (dependency lõi của
`mlflow`, không phải extra) tự nó đã ghim `starlette<1`.

**Phát hiện quan trọng**: xung đột này đã tồn tại **tiềm ẩn từ Phase 4**
(khi `libs/pyproject.toml` thêm `mlflow==3.12.0` làm dependency chung, bump
`0.2.0` → `0.3.0`) — `materialize-job` (đã dùng `feast[redis,aws]==0.66.0`
từ Phase 3) đáng lẽ đã build lỗi ngay từ đó, nhưng không lộ ra vì
`materialize-job` không được rebuild lại sau mốc đó (Docker tái dùng layer
cache cũ, image chạy thực tế vẫn là bản built trước khi `libs` có `mlflow`).
Xác nhận bằng `docker compose build --no-cache materialize-job` — build lỗi
y hệt serving.

## Quyết định

Hạ `feast[redis,aux]` xuống `0.55.0` — version cuối cùng còn tương thích
`starlette<1` mà `pip` xác nhận resolve sạch cùng `mlflow==3.12.0` (kiểm
tra bằng `pip install --dry-run`, không đoán mò). Áp dụng cho **cả hai**
`requirements/materialize.txt` và `requirements/serving.txt` (không chỉ
`serving`) — dù `materialize-job` tự nó không xung đột (không có mlflow
trong image đó), giữ **cùng một version feast** ở mọi nơi để loại trừ rủi
ro lệch format registry Feast (`s3://feast-offline-store/registry.db`,
ghi bởi `materialize-job`, đọc bởi cả `materialize-job` lẫn `serving`)
giữa 2 version cách nhau 11 minor release.

`redis` (redis-py) trong `requirements/serving.txt` **không ghim cứng** —
`feast[redis]==0.55.0` giới hạn `redis<5`, xung đột với pin `==5.2.1` ban
đầu. Để `feast` tự kéo bản tương thích (`redis==4.6.0`) — đã xác nhận đủ
mọi method `serving/` cần (`get/set/delete/rpush/ltrim/expire/lrange`).
`fastapi`/`uvicorn` cũng không ghim cứng, lý do tương tự (cần chỗ cho pip
tự giải đúng bản tương thích `starlette`).

## Lý do

- `mlflow==3.12.0` bắt buộc phải khớp version server đã dựng từ Phase 0
  (ADR 0002) — hạ mlflow không phải lựa chọn hợp lý.
- Đã kiểm tra thật bằng `pip install --dry-run` (không đoán version), theo
  đúng nguyên tắc đã rút ra ở Phase 1 (dlt refactor): xác minh hành vi thật
  của thư viện thay vì tin tài liệu/giả định.
- Chưa kiểm chứng được liệu registry Feast có tương thích ngược giữa 0.55
  và 0.66 hay không (không có tài liệu offline để tra) — nên chọn phương
  án an toàn nhất: đồng bộ 1 version duy nhất ở mọi nơi, loại bỏ hẳn câu
  hỏi này thay vì rủi ro đoán sai.

## Hệ quả

- `requirements/materialize.txt`, `requirements/serving.txt` cùng ghim
  `feast[...]==0.55.0`.
- **Phải kiểm chứng lại toàn bộ chuỗi Phase 3** (`feast apply`,
  `feast materialize-incremental`, `get_online_features()`) sau khi hạ
  version — xem `docs/modules/phase-3-feature-store.md` (mục bổ sung) và
  `docs/modules/phase-5-serving.md` cho kết quả kiểm chứng thật.
- `docs/modules/phase-3-feature-store.md` cần thêm 1 đoạn ghi chú ngày
  (như các bug đã ghi nhận ở phase khác) — không đổi trạng thái phase
  (vẫn Hoàn thành), chỉ đổi version dependency.
- Bài học quy trình: khi 1 image lâu ngày không rebuild, thay đổi ở
  `libs/pyproject.toml` có thể "ẩn" một xung đột thật sự cho tới lần build
  sạch tiếp theo. Cân nhắc thêm `docker compose build --no-cache` định kỳ
  (hoặc ở CI, Phase 8) để bắt sớm loại lỗi này thay vì phát hiện muộn.

## Tham chiếu

`requirements/materialize.txt`, `requirements/serving.txt`,
`libs/pyproject.toml` (dependency `mlflow`, thêm ở Phase 4/ADR liên quan
`docs/modules/phase-4-training.md`); `docs/modules/phase-3-feature-store.md`;
`docs/modules/phase-5-serving.md`.
