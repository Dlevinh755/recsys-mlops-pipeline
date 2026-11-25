# 0015 — Huấn luyện nhiều ứng viên AGF trong một lần chạy, chọn trên validation

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-24
- **Phase liên quan**: Phase 4 (training), Phase 10 (AGF)

## Bối cảnh

`train_agf` chỉ huấn luyện một cấu hình (AGF-GRU4Rec, chốt ở Chương 3 theo
`experiment/results/model_selection.md`). Trên dữ liệu của pipeline (khác bộ paper:
37% item lạ ở tập test) chưa biết trước cấu hình nào tốt hơn, nên mỗi lần train lại là
dịp chọn lại. Cổng promote (ADR 0013) so người thắng với champion, nhưng người thắng
vẫn do một cấu hình duy nhất quyết định.

## Quyết định

1. **`train_agf --candidates a,b,c`** (env `TRAIN_CANDIDATES`, truyền qua
   `docker-compose.yml`): huấn luyện từng ứng viên trên **cùng split** (split chỉ phụ
   thuộc dữ liệu và seed, không phụ thuộc backbone — test xác nhận mọi ứng viên có cùng
   số mẫu valid). **Mặc định `gru4rec`** = cấu hình cũ, một run, hành vi không đổi (DAG
   `dag_training` giữ nguyên).
2. **Bảng ứng viên** (`jobs/training/candidates.py::CANDIDATE_OVERRIDES`, ghi đè
   `MethodConfig`, mọi tham số khác dùng chung): `gru4rec` (gate, mặc định),
   `sasrec` (attention thay GRU), `gru4rec_sum` (`fusion=sum`, h = e + f),
   `gru4rec_idonly` (`fusion=id_only`, đối chứng "thuộc tính có giúp không", tắt mặc định).
   Chỉ gồm họ AGF: FDSA, SASRecF, DIFSR, BERT4Rec, BPR... chỉ có ở RecBole, không nạp
   được qua `MethodConfig`, port là ngoài phạm vi. Ghi ở Chương 4: "đa dạng trong họ AGF,
   chưa gồm các kiến trúc ngoài họ".
3. **Mỗi ứng viên một run MLflow** cùng experiment, tag `candidate_id`,
   `candidate_group` (chung một lần chạy) và `candidate_selected`; `model_type` theo
   backbone (`agf-gru4rec`/`agf-sasrec`, trước đây cứng `agf-gru4rec`). **Chỉ người
   thắng được đăng ký** (một version duy nhất); tên model đăng ký vẫn
   `reco-mlops-agf`, không đụng `libs`.
4. **Luật chọn** (hàm thuần `select_winner`, theo quy tắc ADR 0008 — chọn đơn giản hơn trừ
   khi hơn rõ): ứng viên mặc định là `gru4rec` (hoặc ứng viên đầu danh sách nếu không có
   nó). Ứng viên khác chỉ đủ điều kiện khi NDCG@10 valid trung bình **cao hơn mặc định
   và hiệu theo cặp từng mẫu vượt 1 sai số chuẩn của hiệu**. Nhiều ứng viên đủ điều kiện
   → lấy `val_ndcg_at_10` cao nhất, hoà → ít tham số hơn → thứ tự danh sách. Không ai đủ
   điều kiện → mặc định. **Chọn trên valid; số `test_*` chỉ để báo cáo.**
5. Ghi vào run người thắng: param `selected_from`, `selected_candidate`,
   `selection_rule` (`paired_1se_vs_default`), `selection_default`; metric
   `candidate_<id>_val_ndcg_at_10` và `candidate_<id>_margin_se` cho từng ứng viên,
   `selection_margin_se` của người thắng (0 nếu là mặc định). Khoá theo **id ứng viên**
   thay vì theo backbone như spec, vì `gru4rec` và `gru4rec_sum` cùng backbone.
6. NDCG từng mẫu tính trong `jobs/training/candidates.py::ndcg_per_sample` (cùng giao
   thức xếp hạng với `trainer.evaluate`, có kiểm tra trung bình khớp tới 1e-6), không sửa
   `libs`.
7. Cổng ADR 0013 không đổi: champion được chấm trên split chung và so với run người
   thắng (champion và người thắng có thể khác backbone; chấm qua `seq_output`, không cần
   cầu nối).

## Lý do

- Serving đã được kiểm tra trước khi làm (điều kiện dừng của spec): các test mới trong
  `serving/tests/test_score_candidates.py` chạy `sasrec`, `fusion=sum`, `id_only` và cho
  điểm khớp cả `item_table @ state` lẫn đường full-ranking của training. Serving không
  cần sửa.
- Quy tắc 1 SE thay vì "cao nhất thắng": chênh lệch benchmark giữa hai backbone hàng đầu
  (0,0078) nhỏ hơn 1 std của hiệu; chọn theo điểm thô trên ~1,5K mẫu chủ yếu chọn nhiễu.
- Phương án đăng ký tất cả ứng viên rồi để cổng promote chọn: làm registry đầy phiên bản
  không dùng và bắt cổng promote so nhiều version cùng lúc.

## Kết quả chạy thật (2026-11-24, 3 ứng viên, 1.517 mẫu valid, champion = v8)

| Ứng viên | `val_ndcg_at_10` | hiệu so với `gru4rec` | z (hiệu/SE) | đủ điều kiện | `test_ndcg_at_10` | tham số |
|---|---|---|---|---|---|---|
| `gru4rec` (mặc định) | 0,0847 | — | — | — | 0,0767 | 125.504 |
| `sasrec` | 0,0844 | −0,0003 | −0,06 | không | 0,0803 | 197.504 |
| `gru4rec_sum` | 0,0703 | −0,0144 | −2,82 | không | 0,0628 | 117.184 |

Người thắng: `gru4rec`, đăng ký thành version 9. Champion v8 (cùng cấu hình và dữ liệu)
chấm trên cùng split ra đúng 0,0847, nên cổng ra `same_split`, 0,0847 ≤ 0,0847 +
`min_delta` → **không promote** (`--dry-run`; đúng hành vi `>` chặt: retrain y hệt không
đẩy alias). Đồng thời đây là phép kiểm tra thực tế rằng đường chấm champion (ADR 0013)
tái tạo được điểm của một model khi dữ liệu và seed giống nhau.

Hai điều không được rút ra từ số này: `sasrec` thấp hơn `gru4rec` trên valid nhưng cao hơn
trên test (0,0803 so với 0,0767) — với 1 seed và ~1,5K mẫu không đủ để nói ai tốt hơn; và
bảng chỉ là một lần chạy, chưa lặp qua nhiều seed. Run MLflow: ba run cùng
`candidate_group`, run người thắng mang `selected_from`.

## Hệ quả

- Thời gian train gấp ba (~6 phút cho 3 ứng viên có nhánh text, so với ~2 phút).
  `.env.example` ghi `TRAIN_CANDIDATES=gru4rec` mặc định (đã đổi thành ba ứng viên ở ADR 0016).
- Registry chỉ giữ người thắng: xem ứng viên thua trong MLflow (`candidate_group`).
- **Winner's curse**: chọn 1 trong K trên ~1,5K mẫu valid vẫn lạc quan nhẹ; K−1 phép so
  với mặc định không hiệu chỉnh đa so sánh. Không tuyên bố "tốt hơn có ý nghĩa thống kê".
- `sasrec` và `fusion=sum` chưa từng được promote hay phục vụ thật trên stack; mới có test
  điểm số của serving (`serving/tests/test_score_candidates.py`, chưa chạy trong Jenkins).
- Ứng viên đưa vào DAG chỉ bằng `TRAIN_CANDIDATES` trong `.env` (restart scheduler vì cấu
  hình compose được cache theo tiến trình).

## Tham chiếu

`jobs/training/candidates.py`, `jobs/training/train_agf.py`,
`tests/unit/training/test_candidates.py`, `tests/model/test_train_agf_smoke.py`,
`serving/tests/test_score_candidates.py`, `side_doc/SPEC-retrain-loop-and-promote-gate.md`
mục F, ADR 0008, 0013.
