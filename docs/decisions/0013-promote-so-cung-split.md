# 0013 — Cổng promote so sánh trên cùng một tập đánh giá

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-24
- **Phase liên quan**: Phase 4 (promote), Phase 10 (AGF)

## Bối cảnh

`promote.py` (Phase 4) so `val_ndcg_at_10` của run challenger với
`val_ndcg_at_10` của run đã sinh ra model production, mỗi số lấy từ run riêng
của nó. Hai số đó đo trên **hai tập valid khác nhau** mỗi khi dữ liệu đổi giữa
hai lần train. Đo ngày 2026-11-24 sau khi nạp `incoming/`:

| | Valid | `val_ndcg_at_10` | Pop trên cùng split | Lift so với Pop |
|---|---|---|---|---|
| v4 (production) | 1.378 mẫu | 0,0920 | 0,0442 | 2,08× |
| v5 (challenger) | 1.517 mẫu | 0,0847 | 0,0373 | 2,27× |

Cổng từ chối v5 vì 0,0847 < 0,0920, trong khi baseline Popularity cũng tụt trên
tập mới: v5 không tệ hơn v4, chỉ gặp tập valid khó hơn.

## Quyết định

1. **Chấm champion trên split của challenger.** `train_agf` nạp model
   `production` (nếu có), chấm trên đúng mẫu valid/test vừa dùng cho
   challenger và ghi vào **run của challenger**: param `champion_version`,
   metric `champion_{val,test}_{ndcg,recall}_at_{10,20}_same_split`
   (`jobs/training/champion_eval.py`).
2. **Luật trong `promote.py`** (hàm thuần `decide_promotion`):
   1. không có production → promote (`comparison: "first"`);
   2. run có `champion_val_ndcg_at_10_same_split` **và** `champion_version`
      trùng version production hiện tại → promote khi
      `val_ndcg_at_10 > champion_same_split + min_delta` (`"same_split"`);
   3. còn lại → luật cũ trên số riêng của từng run (`"legacy"`).
3. **`>` chặt thay cho `>=`** ở nhánh `same_split`: retrain y hệt (cùng dữ liệu,
   cùng seed) cho điểm bằng nhau, không được đẩy alias. Nhánh `legacy` giữ `>=`.
4. **`--min-delta`** (mặc định 0,0) và **`--dry-run`** (in quyết định, không đổi
   alias). JSON thêm `comparison`, `reason`, `champion_metric_same_split`,
   `candidate_lift_over_pop`, `production_lift_over_pop`, `min_delta`.
5. **Champion lệch version** (production đổi giữa lúc train và lúc promote, ví
   dụ rollback tay) → số same-split đã cũ, rơi về `legacy` và ghi lý do.
6. `train_agf --no-champion` bỏ bước chấm. Nếu model production **không nạp
   được** thì job dừng (không train tiếp), để cổng không rơi về so sánh lệch
   split một cách im lặng.

## Cách chấm champion

Theo đúng cách `serving` chấm (`score_candidates`), không qua `trainer.evaluate`
(hàm đó cần thuộc tính item). Vocab hai version khác nhau nên mẫu của challenger
được dịch qua `product_id`:

- item lịch sử champion không biết → bỏ khỏi chuỗi;
- target champion không biết → tính là **hụt** (không đề xuất được item chưa
  từng thấy); chuỗi rỗng sau khi bỏ → hụt;
- cột item champion không biết có điểm 0,0 (quy tắc trung tính của serving);
- rank dùng `trainer.full_ranks`: xếp hạng toàn bộ catalog của challenger, chỉ
  che `[PAD]`, top-20.

Số mẫu bị ảnh hưởng ghi thành param (`champion_{val,test}_unknown_target`,
`_no_history`). Khi catalog thêm nhiều item mới, champion chịu thiệt vì không
đề xuất được chúng; số mẫu hụt này lộ ra trong run để đánh giá mức thiên lệch.

## Lý do

- Phương án "so lift so với Pop" (`val_ndcg/pop_val_ndcg`) tránh phải nạp model
  cũ nhưng vẫn là hai tập valid khác nhau và giả định Pop chuẩn hoá được độ khó
  của tập; chỉ giữ làm số báo cáo (`*_lift_over_pop`).
- Train lại champion trên dữ liệu mới: đắt gấp đôi và đổi câu hỏi (so kiến trúc
  thay vì so model đang chạy).
- Kiểm tra trước khi tin: trên fixture, champion chấm qua đường này tái tạo
  `val_ndcg_at_10` và `test_ndcg_at_10` của chính nó tới 4 chữ số thập phân
  (`tests/model/test_train_agf_smoke.py`).

## Hệ quả

- **Thay đổi hành vi**: `>=` → `>` cho nhánh mới. Lần đầu có production vẫn
  promote như cũ.
- **Chạy thật 2026-11-24** (v6, cùng Gold với v5, champion = v4): v6 đạt 0,0847
  trên valid; v4 chấm trên cùng split đạt **0,0680** (0 mẫu hụt vì item mới).
  Cổng ra `same_split`, `would_promote: true` (0,0847 > 0,0680; lift 2,27× so với
  2,08×). Version 5 (train trước thay đổi này, không có số same-split) vẫn chỉ
  được xét theo `legacy` và bị từ chối như cũ. Chạy với `--dry-run`, alias
  `production` vẫn ở v4.
- Chưa có kiểm định thống kê: 1 seed, ~1,5K mẫu valid, nên hiệu 0,0167 là điểm
  ước lượng chứ chưa có khoảng tin cậy. Bootstrap theo cặp (A3 của spec) chưa
  làm; không tuyên bố "tốt hơn có ý nghĩa thống kê".
- Champion chỉ thấy tối đa `net.max_len` item cuối của chuỗi mà challenger đã cắt
  sẵn theo `max_len` của nó; hiện cả hai cùng 10.

## Tham chiếu

`jobs/training/promote.py`, `jobs/training/champion_eval.py`,
`jobs/training/train_agf.py`, `tests/unit/training/test_promote.py`,
`tests/unit/training/test_champion_eval.py`, `tests/model/test_train_agf_smoke.py`,
`docs/modules/phase-4-training.md` mục 2.6, `docs/modules/phase-10-agf.md` mục 7.5.
