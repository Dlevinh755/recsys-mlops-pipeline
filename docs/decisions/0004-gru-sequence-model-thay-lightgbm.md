# 0004 — GRU (sequence model) thay LightGBM làm model chính

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-09-06
- **Phase liên quan**: Phase 4 (Training & Model Registry), Phase 9 (Sequence
  Model — gộp vào Phase 4), ảnh hưởng thiết kế Phase 3 (Feature Store)

## Bối cảnh

`bao-cao-ky-thuat.md` mục 7.6 và `de-xuat-trien-khai.md` Phase 9 thiết kế
GRU4Rec (rút gọn) là **nhánh mở rộng tùy chọn**, chỉ triển khai **sau khi**
MVP1 dựa trên LightGBMRanker (Phase 4) đã hoàn chỉnh và demo được — lý do
nêu rõ: LightGBM và GRU nhận input khác kiểu hoàn toàn (bảng phẳng theo
user/item tại 1 thời điểm vs. chuỗi hành vi theo thời gian), nên tách phase
để không chặn đường găng MVP1.

Người dùng quyết định: dùng luôn dữ liệu thật (Amazon Fashion dataset) làm
nguồn dữ liệu sau này, và muốn **GRU là model chính ngay từ đầu, không dùng
LightGBM nữa** — không phải "làm LightGBM trước, GRU sau" như kế hoạch gốc.

## Quyết định

- **Phase 4** đổi nội dung: huấn luyện **GRU4Rec rút gọn** (Embedding → GRU
  1 lớp → Linear + sampled softmax, đúng kiến trúc đã mô tả ở mục 7.6 gốc)
  thay vì LightGBMRanker. Không xây `negative_sampling.py`/`train_ranker.py`
  kiểu LightGBM.
- **Phase 9** (Sequence Model, mở rộng) coi như **gộp vào Phase 4** — không
  còn là nhánh tùy chọn làm sau, vì nội dung của nó (GRU4Rec) giờ chính là
  model chính. `build_sequence_dataset.py`/`train_sequence.py` (tên file
  theo kế hoạch gốc ở Phase 9) chuyển thành việc của Phase 4.
- **Phase 3 (Feature Store) thiết kế lại theo hướng chuỗi**: `jobs/features/
  build_user_features.py` sinh ra **chuỗi item_id gần nhất theo thời gian
  của mỗi user** (từ Gold/Silver interactions), không phải các đặc trưng
  tổng hợp dạng rolling-window (count/avg theo 7/30/90/365 ngày) vốn phù
  hợp cho LightGBM. Đây là thay đổi trực tiếp so với những gì đã thảo luận
  trước ADR này.
- `jobs/features/build_item_features.py` (đã có từ Phase 2 dưới dạng
  `gold.item_features`) **vẫn giữ nguyên** — đặc trưng tổng hợp theo item
  vẫn hữu ích cho candidate filtering/business rule, độc lập với việc model
  ranking dùng kiểu input nào.
- Interface `libs/src/reco_mlops_libs/ranking/base.py`
  (`predict(user_id, candidate_items) -> scores`) **không đổi** — đây chính
  là lý do interface này được thiết kế từ đầu (đã ghi trong
  `bao-cao-ky-thuat.md` mục 7.5): tầng serving không biết/không cần biết
  model bên trong nhận DataFrame phẳng hay tensor chuỗi.

## Lý do

- Người dùng có kế hoạch dùng bộ dữ liệu Amazon Fashion thật — dữ liệu
  hành vi mua sắm thời trang có tính chuỗi/thời vụ rõ, phù hợp với sequence
  model hơn là feature tĩnh.
- Tránh làm 2 lần: nếu vẫn xây LightGBM trước rồi mới đổi sang GRU sau,
  toàn bộ `jobs/features/` hướng rolling-window (Phase 3) và
  `jobs/training/` hướng bảng phẳng (Phase 4) phải viết lại — build thẳng
  theo hướng chuỗi từ Phase 3 tiết kiệm công sức hơn.
- Đánh đổi bị mất: `bao-cao-ky-thuat.md` mục 7.6 coi việc **so sánh công
  bằng LightGBM vs GRU trên cùng bộ metric** là một phần giá trị khoa học
  của khóa luận ("kết quả có thể là sequence model chưa vượt trội — vẫn là
  so sánh có giá trị"). Bỏ LightGBM nghĩa là **mất phần so sánh này** — ghi
  nhận đây là đánh đổi có chủ đích, không phải bỏ sót.

## Hệ quả

- `docs/de-xuat-trien-khai.md` Phase 4 checklist (LightGBMRanker,
  `negative_sampling.py`, `train_ranker.py`) **không còn áp dụng** — thay
  bằng nội dung Phase 9 gốc (GRU4Rec, `build_sequence_dataset.py`,
  `train_sequence.py`). Phase 9 trong tài liệu coi như đã hoàn thành mục
  tiêu của nó sớm hơn, gộp vào Phase 4.
- **Phase 3 cần thiết kế lại phạm vi** trước khi code (đang chờ xác nhận
  chi tiết: độ dài chuỗi tối đa, có cần thêm side-feature dạng số bên cạnh
  chuỗi hay không).
- **Liên hệ với Phase 5 (`/interact` + `recency_boost.py`)**: thiết kế gốc
  dùng `recent_items:{user_id}` trong Redis chỉ để **re-rank hậu kỳ** (boost
  thủ công sau khi model đã chấm điểm). Khi model chính là GRU, chuỗi này
  **chính là input trực tiếp của model**, không chỉ là tín hiệu boost —
  điểm này cần cân nhắc lại khi làm tới Phase 5, nhưng **chưa cần quyết định
  ngay bây giờ** (đang ở Phase 3).
- Dữ liệu Amazon Fashion: chưa triển khai (theo kế hoạch của người dùng là
  "sau này") — không có thay đổi code nào cho việc này ở thời điểm ADR này
  được ghi nhận. Cần một ADR/ghi chú riêng khi thực sự tích hợp dataset đó.

## Tham chiếu

`docs/bao-cao-ky-thuat.md` mục 7.5, 7.6; `docs/de-xuat-trien-khai.md` Phase
4, Phase 9; `libs/src/reco_mlops_libs/ranking/base.py` (chưa tạo, sẽ tạo ở
Phase 4).
