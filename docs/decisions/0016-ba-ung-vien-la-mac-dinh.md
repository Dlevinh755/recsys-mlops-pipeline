# 0016 — Ba ứng viên AGF là mặc định

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-25
- **Phase liên quan**: Phase 4 (training), Phase 10 (AGF)
- **Sửa đổi**: quyết định "mặc định một ứng viên `gru4rec`" của [ADR 0015](0015-nhieu-ung-vien-agf-chon-tren-valid.md)

## Bối cảnh

ADR 0015 giữ mặc định một ứng viên để hành vi cũ không đổi. Khi chạy lại pipeline từ bản
clone sạch (2026-11-25), `dag_training` chỉ tạo một run MLflow và registry chỉ có một model,
trong khi báo cáo mô tả cơ chế ba ứng viên (Bảng 4.14). Người dùng cuối phải tự đặt
`TRAIN_CANDIDATES` mới thấy cơ chế này hoạt động.

## Quyết định

1. Mặc định của `docker-compose.yml` (`${TRAIN_CANDIDATES:-gru4rec,sasrec,gru4rec_sum}`) và của
   `.env.example` là **ba ứng viên** `gru4rec,sasrec,gru4rec_sum`. `dag_training`,
   `dag_continuous_training` và `make train` đều theo mặc định này.
2. `gru4rec` vẫn là cấu hình gốc và là **mốc so sánh của luật chọn** (ADR 0015 mục 4). Khi
   cần một ứng viên: `TRAIN_CANDIDATES=gru4rec` (rồi restart scheduler với Airflow).
3. Giá trị dự phòng trong mã (`candidates.DEFAULT_CANDIDATES = ("gru4rec",)`, dùng khi gọi
   `run_training` không truyền ứng viên, như unit/model smoke test) **không đổi**: test và
   Jenkins không bị chậm gấp ba.

## Hệ quả

- Mỗi lần `dag_training` mất ~6–7 phút thay vì ~2 phút; chuỗi `dag_continuous_training`
  dài thêm tương ứng.
- Registry vẫn chỉ giữ người thắng; ứng viên thua nằm trong MLflow (`candidate_group`).
- Các hạn chế của ADR 0015 (winner's curse, một seed) giữ nguyên.
- Cần cập nhật `.env` của bản clone hoặc máy đã có `.env` cũ (`.env.example` chỉ copy lần đầu).
