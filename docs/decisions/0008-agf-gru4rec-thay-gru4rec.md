# 0008 — AGF-GRU4Rec thay GRU4Rec (chỉ ID) làm model chính; thêm cây danh mục sản phẩm

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-14
- **Phase liên quan**: Phase 4 (Training), Phase 5 (Serving), Phase 1–2 (cột
  mới `category_path` ở source-db → Bronze → Silver → Gold). Ghi nhận phần
  triển khai ở `docs/modules/phase-10-agf.md`.

## Bối cảnh

ADR 0004 chọn GRU4Rec rút gọn chỉ dùng item ID (Embedding → GRU → Linear,
sampled softmax, CPU) làm model chính. Kết quả thực tế (`docs/model_card.md`):
`val_ndcg_at_10` ≈ 0,005–0,008, chưa có baseline so sánh, và **không chấm
điểm được item chưa có trong train** — trong khi `amazone_data/DATA.md` mục
4.1 ghi 30,1% dòng của `test.parquet` là item lạnh, và `serving` phải
fallback sang popularity cho các item đó.

Repo thực nghiệm `../experiment/` (git repo riêng, quyết định 2026-11-03 ở
`experiment/STATUS.md`) đã benchmark 11 model trên 2 bộ Amazon Reviews 2023
(Scientific, Games; 3 seed; kiểm định Wilcoxon + Holm) và đề xuất **AGF**
(Adaptive Gated Fusion): trộn embedding ID với thuộc tính item (danh mục,
store, giá, text) bằng một cổng có tín hiệu độ phổ biến, dùng biểu diễn đã
trộn `h` ở **cả đầu vào lẫn lớp chấm điểm**. Biên bản chọn phương pháp
(`experiment/results/model_selection.md`, chỉ dùng validation):

| | valid N@10 Scientific | valid N@10 Games | điểm chuẩn hoá theo SASRec |
|---|---|---|---|
| AGF-SASRec | 0,03254 | 0,05429 | 1,1998 ± 0,0076 |
| **AGF-GRU4Rec** (chọn) | 0,03214 | 0,05432 | 1,1920 ± 0,0086 |
| FDSA (baseline có thuộc tính mạnh nhất) | 0,03021 | 0,05382 | 1,1483 ± 0,0099 |
| GRU4Rec (chỉ ID, cùng giao thức) | 0,0214 | 0,0481 | 0,9133 ± 0,0021 |

AGF-GRU4Rec được chọn thay AGF-SASRec vì chênh dưới 1 std của hiệu và ít
tham số hơn (quy tắc 3, `experiment/docs/03-tich-hop-mlops.md`). Độ dài chuỗi
L = 10 không giảm metric so với L = 50 (valid 0,03207 vs 0,03214) — khớp với
`gold.user_features` vốn chỉ giữ 10 item gần nhất. Gói bàn giao thuần PyTorch
đã kiểm chứng không cần RecBole: tái tạo valid N@10 trong ± 2 std, bảng `H`
tính trước khớp điểm trực tiếp với sai số 0,0
(`experiment/configs/handoff/export_manifest.json`).

Thực nghiệm dùng **cây danh mục** (`categories`, tối đa 3 cấp) làm thuộc tính
chính. Pipeline hiện bỏ mất cây này ngay khi seed `source-db`
(`scripts/seed_source_db.py::_clean_category` chỉ giữ `main_category`, ~8 giá
trị cho 1.565 item).

## Quyết định

1. **Model chính** đổi từ GRU4Rec chỉ-ID sang **AGF-GRU4Rec** theo đúng cấu
   hình đã chốt ở thực nghiệm (`d = 64`, `max_len = 10`, `dropout = 0,2`,
   `fusion = gate`, `output_fusion = true`, `use_popularity = true`,
   `use_text = true`, Adam lr 1e-3, batch 2048, CE trên toàn bộ item, early
   stopping theo valid NDCG@10, patience 10, tối đa 200 epoch).
   - Code chép nguyên từ thực nghiệm vào `libs/src/reco_mlops_libs/ranking/agf/`
     (`agf_core.py`, `features.py`, `trainer.py`), đầu mỗi file ghi nguồn và
     commit SHA. Chỉ đổi import cho khớp package. `libs` lên 0.7.0.
   - Job mới `jobs/training/train_agf.py` (DAG `dag_training`, `make train`).
     `jobs/training/train_sequence.py` (GRU4Rec cũ) giữ lại để chạy tay làm
     mốc so sánh (`make train-gru4rec`), ghi vào registry cũ
     `reco-mlops-gru4rec`.
   - Registry model mới: **`reco-mlops-agf`** (hằng số
     `REGISTERED_MODEL_NAME` ở `reco_mlops_libs/ranking/base.py`, dùng chung
     cho training/promote/serving/candidates/monitoring). Không ghi chung
     registry với GRU4Rec vì cách tính metric khác nhau (AGF: leave-one-out
     valid = item kế cuối, full softmax; GRU4Rec cũ: valid = item cuối,
     sampled softmax) — promote theo champion/challenger trên 2 metric không
     so sánh được sẽ sai.
   - Không cài RecBole vào image nào.
2. **Thuộc tính item**: thêm cột **`category_path`** (TEXT, nullable — cây
   danh mục gốc của Amazon nối bằng `" > "`) vào `source-db.products` và các
   bảng `bronze.products`, `silver.products`, `gold.item_features`. Lưu dạng
   chuỗi thay vì mảng để không phải đổi kiểu cột qua dlt/Parquet/Iceberg
   (list type), tách lại thành list trong job train. Các thuộc tính còn lại
   dùng cột sẵn có: `brand` → store, `price` → bucket giá, `title` +
   danh mục → text.
3. **Ánh xạ sang quy tắc của thực nghiệm** (`features.py`, không sửa):
   `categories` = `category_path` tách theo `" > "` (rỗng → `category`),
   `store` = `brand`, `price` = `price`, `title` = `title`. Vocab, ngưỡng
   `[UNK]` và biên bucket giá **fit lại mỗi lần train**, lưu thành artifact.
   Text embedding (`all-MiniLM-L6-v2`, đóng băng) tính trong job train, không
   tính trong `serving`.
4. **Serving**: chấm điểm bằng bảng `H` tính trước (`item_table.npy`, artifact
   của chính run được promote), vẫn qua interface `predict(user_id,
   candidate_items)` (nguyên tắc #7). Item có trong catalog lúc train nhưng
   chưa có tương tác (item lạnh) được chấm từ thuộc tính (`e^id = 0`, `π = 0`).

## Lý do

- Thực nghiệm có kiểm soát cho thấy AGF-GRU4Rec hơn GRU4Rec chỉ-ID +50% valid
  N@10 trên Scientific và +13% trên Games, có ý nghĩa thống kê; chi phí phục
  vụ gần bằng backbone (H tính trước, latency CPU p95 0,32 ms ở L = 10).
- AGF giải quyết trực tiếp hạn chế item lạnh của pipeline mà không cần
  fallback riêng cho item đã có trong catalog.
- Ablation thực nghiệm cho thấy **thuộc tính là nguồn lợi ích chính** (bỏ
  thuộc tính: −27,5%), nên giữ cây danh mục đúng như thực nghiệm thay vì chỉ
  dùng `main_category` gần như hằng. Người dùng chọn phương án này
  (2026-11-14) dù phải đổi schema Phase 1–2 và cold start lại.
- Giữ GRU4Rec cũ chạy tay để vẫn có mốc so sánh trên chính dữ liệu Cell
  Phones — khắc phục một phần đánh đổi "mất phần so sánh" đã ghi ở ADR 0004.

## Hệ quả

- **Lệch so với thực nghiệm (phải nêu khi trình bày)**: dữ liệu pipeline là
  Cell Phones k-core 8 (~16K tương tác, ~1,5K item), không phải Scientific/
  Games 5-core; train trên CPU; chuỗi train lấy từ `gold.user_features` (tối
  đa 10 item gần nhất/user, nên mẫu train và tần suất π chỉ đếm trên 10 item
  đó); `store` của pipeline là `brand` của source-db. Kết quả trên Cell Phones
  **không** dùng để chọn phương pháp hay đưa vào paper (`experiment/docs/03`).
- **Schema evolution**: thêm 1 cột nullable ở 3 bảng Iceberg + Postgres. Cách
  áp dụng: cold start (`make clean && make up` sau khi sinh lại
  `infra/source-db/init/seed_data_amazon.sql`), xem
  `docs/modules/phase-10-agf.md` mục "Cách chạy lại".
- **Dependency mới** trong image `training`: `sentence-transformers`, `pyyaml`;
  lần train đầu tải model MiniLM (~90 MB) từ Hugging Face vào cache của
  container (không phải state bền vững — tải lại được, nguyên tắc #6). Có cờ
  `--no-text` để train không cần mạng (dùng trong smoke test CI).
- `serving` không thêm dependency (vẫn torch + numpy). `similar_items` dùng
  cosine trên `H` thay vì bảng ID — item lạnh cũng có danh sách tương tự.
- Nguyên tắc kiến trúc ở `CLAUDE.md` giữ nguyên, không thêm ngoại lệ.

## Tham chiếu

- Thực nghiệm: `../experiment/results/model_selection.md`,
  `../experiment/docs/02-mo-hinh-de-xuat.md`,
  `../experiment/docs/03-tich-hop-mlops.md`,
  `../experiment/configs/handoff/export_manifest.json`.
- Code: `libs/src/reco_mlops_libs/ranking/agf/`, `jobs/training/train_agf.py`,
  `jobs/training/baselines.py`, `serving/app/core/model_loader.py`,
  `jobs/candidates/similar_items.py`.
- Module doc: `docs/modules/phase-10-agf.md`.
