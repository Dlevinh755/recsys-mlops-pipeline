# Phase 10 — AGF-GRU4Rec thay GRU4Rec chỉ-ID (ADR 0008)

> Tài liệu as-built. Quyết định và lý do: [ADR 0008](../decisions/0008-agf-gru4rec-thay-gru4rec.md).
> Phương pháp (kiến trúc, quy tắc thuộc tính, cấu hình) đến từ repo thực
> nghiệm `../experiment/` — bàn giao theo `experiment/docs/03-tich-hop-mlops.md`
> mục "Thay đổi phía pipeline". Phase này không có trong `de-xuat-trien-khai.md`
> (Phase 9 đã gộp vào Phase 4 theo ADR 0004); DoD lấy từ tài liệu bàn giao đó.

**Trạng thái kiểm chứng (2026-11-14)**: code + test đã viết; chỉ phần không
cần torch/docker đã chạy được trong môi trường viết code (13 unit test thuần
pandas/numpy pass, toàn bộ file mới `py_compile` sạch). **Chưa chạy**: build
image, test cần torch (`tests/model`, `serving/tests/test_score_candidates.py`),
cold start với `category_path`, lần train đầu trên dữ liệu thật — xem mục 5.

## 1. Mục tiêu ban đầu

Theo `experiment/docs/03-tich-hop-mlops.md`:

- ADR 0008 thay GRU4Rec, dẫn chứng từ `experiment/results/`.
- Chép `agf_core.py`, `trainer.py`, `features.py` vào `libs/` (ghi nguồn +
  commit SHA), tăng version `libs`, giữ interface `predict(user_id,
  candidate_items)`.
- Thêm thuộc tính item vào `gold.item_features`; text embedding tính trong job
  train, không trong `serving`.
- Chỉnh độ dài chuỗi theo kết quả 10 vs 50 (→ L = 10).
- Không cài RecBole vào image nào.

Thêm theo yêu cầu người dùng (2026-11-14): giữ **cây danh mục** như thực
nghiệm (đổi schema Phase 1–2), baseline popularity để so sánh, cập nhật model
card.

## 2. Những gì đã triển khai

### 2.1 Cột `category_path` qua mọi tầng

| Tầng | Thay đổi |
|---|---|
| `source-db.products` | Cột `category_path TEXT` (nullable) — `infra/source-db/init/schema.sql` |
| Seed | `scripts/seed_source_db.py::_category_path` nối `meta.parquet.categories` bằng `" > "`; cả 2 mode (load trực tiếp, `--dump-sql`) |
| Extract (Phase 1) | Không sửa — truy vấn trang lấy mọi cột (`cur.description`) |
| `bronze.products` | Field 13 `category_path` (optional); file staging cũ không có cột → `fill_missing_optional_columns` |
| `silver.products` | Field 11; `sql/silver/silver_products.sql` chọn thêm cột |
| `gold.item_features` | Field 16; `sql/gold/gold_item_features.sql` chọn + `GROUP BY` thêm cột |

Lưu chuỗi thay vì mảng: không phải đổi kiểu list qua dlt/Parquet/Iceberg/pandera;
job train tách lại (`agf_inputs.split_category_path`).

### 2.2 `libs/` 0.7.0 — `reco_mlops_libs/ranking/agf/`

`agf_core.py` (AGFItemEncoder + AGFSequentialModel), `features.py`, `trainer.py`
chép nguyên từ `experiment` commit `58d50db`; khác biệt duy nhất: 3 dòng header
ghi nguồn và import trong `trainer.py` trỏ vào package. `__init__.py` không
import gì (image không có torch vẫn import được `features`). Thêm:
`ranking/base.py::REGISTERED_MODEL_NAME = "reco-mlops-agf"` và
`mlflow_utils/logging_helpers.py::log_iceberg_snapshots()` (lineage nhiều bảng —
`log_iceberg_snapshot` cũ ghi cố định 2 key nên không gọi 2 lần được).

### 2.3 Training — `jobs/training/train_agf.py`

1. Đọc `gold.item_features` (`product_id, title, category, category_path,
   brand, price`) và `gold.user_features` (`user_id, item_sequence,
   event_time_sequence`).
2. `agf_inputs.py` (thuần pandas): catalog đầy đủ sắp theo `product_id` → khung
   metadata đúng cột `features.py` cần (`category_path`→`categories`,
   `category`→`main_category`, `brand`→`store`); chuỗi → `(user, item,
   timestamp)`; `build_catalog_items` = `trainer.prepare_items` nhưng trên
   **toàn catalog** (item không có tương tác train vẫn có hàng trong `H`).
3. `trainer.train` với `agf_method_config.yaml` (chép từ
   `experiment/configs/handoff/method_config.yaml`).
4. `trainer.export` → artifact `agf/` (`model_state.pt`, `item_table.npy`,
   `item_vocab.json` dạng list, `feature_vocab.json`,
   `price_bucket_edges.json`, `metrics.json`); job **fail** nếu
   `item_table_max_abs_diff ≥ 1e-5`.
5. MLflow: experiment + registry `reco-mlops-agf`; params cấu hình + kích
   thước dữ liệu/vocab + snapshot Iceberg 2 bảng; metric `val_*`, `test_*`,
   `baseline_pop_*`, history theo epoch; `item_vocab.json` gốc dạng
   `{product_id: index}` (định dạng serving/monitoring đã đọc);
   `mlflow.pytorch.log_model` + `register_model`.

CLI: `--config`, `--seed` (2023), `--no-text`, `--epochs`, `--device` (cpu).

### 2.4 Baseline popularity — `jobs/training/baselines.py`

Pop = tần suất trong phần train (cùng `train_counts` AGF dùng cho `π`), xếp hạng
trên toàn bộ item, hoà điểm tách theo chỉ số item; đánh giá trên đúng
`samples["valid"]`/`["test"]` của run → `baseline_pop_val_*`,
`baseline_pop_test_*` cùng run MLflow, so trực tiếp với `val_*`.

### 2.5 Promote, DAG, Makefile

- `promote.py`: mặc định `REGISTERED_MODEL_NAME` từ libs (không còn import từ
  `train_sequence`); `--model-name reco-mlops-gru4rec` vẫn dùng được cho model cũ.
- `dag_training`: `train_agf >> promote >> similar_items`.
- `make train` → `train_agf`; `make train-gru4rec` → GRU4Rec cũ.

### 2.6 Serving — `serving/app/core/model_loader.py`

`LoadedModel` thêm `item_table` (tải `agf/item_table.npy` của chính version
production, kiểm số hàng = vocab + 1). `score_candidates`: `s_u =
net.seq_output(seq[-max_len:], H)`, điểm = `H[candidates] · s_u`, item ngoài
vocab → 0,0. `ServingRanker.predict` giữ nguyên chữ ký (nguyên tắc #7).
Không thêm dependency vào image `serving`.

### 2.7 Candidates & monitoring

- `jobs/candidates/similar_items.py`: cosine trên `H` (thay bảng ID của
  GRU4Rec) — item lạnh cũng có danh sách tương tự từ thuộc tính.
- `jobs/monitoring/model_quality_report.py`: tên registry lấy từ libs.

### 2.8 Hạ tầng

`infra/docker/training/Dockerfile`: lần `pip install` thứ hai (PyPI) đọc
`requirements/training-pypi.txt` (`redis==5.2.1`, `sentence-transformers==3.4.1`);
`CMD` mặc định `jobs.training.train_agf`.

### 2.9 Test

| Test | Chạy ở | Kiểm tra |
|---|---|---|
| `tests/unit/training/test_agf_inputs.py` | `training-job` (hoặc bất kỳ đâu có pandas) | Tách `category_path`; ánh xạ cột; thứ tự chuỗi; `train_counts` bỏ 2 item cuối; mã hoá trùng `features.py`; `[UNK]`/`[MISSING]`; text đúng thứ tự catalog; item lạ → lỗi |
| `tests/unit/training/test_baselines.py` | như trên | Rank popularity, hoà điểm, NDCG/Recall tính tay |
| `tests/model/test_train_agf_smoke.py` | `training-job` (Jenkins `Model Smoke Test` tự nhặt) | `run_training` end-to-end trên fixture, `--no-text`, 2 epoch; artifact `agf/item_table.npy`, `item_vocab.json` |
| `serving/tests/test_score_candidates.py` | image `serving` (chạy tay; cần `pip install pytest`) | Điểm serving = điểm full-ranking của model; cắt `max_len`; kiểm số hàng `H`; từ ADR 0015 chạy cho cả `sasrec`, `fusion=sum`, `id_only` |
| `serving/tests/test_model_reload.py` | `training-job` + stack CI | Đổi sang train 2 version AGF tí hon |

## 3. Khác biệt so với đề xuất ban đầu

- **Lệch so với thực nghiệm** (đã ghi ở ADR 0008): dữ liệu Cell Phones k-core
  8; train CPU; chuỗi train lấy từ `gold.user_features` (≤ 10 item/user) thay
  vì toàn bộ lịch sử; `store` = `brand`; cây danh mục của Cell Phones.
- `trainer.prepare_items` của thực nghiệm chỉ lấy item có trong tương tác;
  pipeline dùng `build_catalog_items` (toàn catalog) để item lạnh vẫn được chấm
  — cùng quy tắc mã hoá, chỉ khác tập item.
- Registry đổi tên (`reco-mlops-agf`) thay vì ghi tiếp `reco-mlops-gru4rec`.
- `serving/tests/test_score_candidates.py` không có stage Jenkins (như các
  test serving khác hiện có — Jenkins chỉ test 3 image).

## 4. Definition of Done — đối chiếu (`experiment/docs/03`)

| Mục | Trạng thái |
|---|---|
| ADR 0008 thay GRU4Rec, dẫn chứng `results/` | ✅ |
| Chép 3 file vào `libs/`, ghi nguồn + SHA, tăng version | ✅ 0.7.0 |
| Giữ interface `predict(user_id, candidate_items)` | ✅ |
| Thuộc tính trong `gold.item_features` | ✅ `category_path` mới + `category`/`brand`/`price`/`title` sẵn có |
| Text embedding trong job train, không trong serving | ✅ |
| Độ dài chuỗi 10 | ✅ `max_len: 10` (khớp `MAX_SEQUENCE_LENGTH`) |
| Không cài RecBole | ✅ |
| Job train pipeline tự train + serve | ✅ chạy thật 2026-11-19 (mục 7); 26 test pass trong image training |

## 5. Việc còn để lại

1. **Sinh lại seed + cold start** (bắt buộc để có `category_path`; môi trường
   viết code không đọc được parquet):

   ```bash
   python scripts/seed_source_db.py --dump-sql infra/source-db/init/seed_data_amazon.sql
   make clean && make up        # volume source-db mới → nạp seed mới
   # rồi chạy lại extract → bronze → silver → gold → user_features → materialize
   ```

2. ~~Build lại image + chạy test cần torch~~ — đã chạy 2026-11-20 trong image training: `pytest tests/unit/training tests/model` → **26 passed** (gồm smoke test train_agf). `serving.tests.test_score_candidates` → 3 test OK trong image serving (2026-11-20). Lệnh gốc:

   ```bash
   docker compose --profile jobs build training-job serving
   docker compose --profile jobs run --rm --entrypoint python training-job -m pytest tests/unit/training tests/model -v
   docker compose run --rm --entrypoint python serving -m unittest serving.tests.test_score_candidates -v
   ```

3. ~~`make train && make promote && make similar-items`~~ — đã chạy 2026-11-19,
   bảng metric đã điền ở `docs/model_card.md` (mục 7).
4. ~~Kiểm tra `sentence-transformers==3.4.1` + `torch==2.9.0`~~ — build image
   `training-job` thành công, train có nhánh text chạy được.
5. ~~`ui/app.py` còn câu "GRU4Rec"~~ — đã sửa thành "AGF-GRU4Rec".
6. ~~8 dòng interaction fixture (`id` 14749–14756) không vào Bronze~~ — đã tìm ra nguyên nhân (state dlt cục bộ cũ) và sửa `make clean`; không khôi phục dòng ở môi trường hiện tại, mục 7.3.
7. Các hạn chế cũ ở `STATUS.md` (image tag `latest` trong Airflow, drift → retrain
   chưa khép kín) không đổi. Jenkins đã chạy thật 2026-11-24 (ADR 0011).

## 7. Kết quả chạy thật (2026-11-19, test tay từng bước)

Cold start → từng job chạy ngoài Airflow, theo thứ tự dưới đây.

### 7.1 Số liệu qua từng tầng

| Bước | Kết quả |
|---|---|
| Source-db (sau seed) | products 687 (682 có `category_path`), users 1.422, interactions 14.756 |
| Extract | products 687, interactions 14.748; chạy lại lần 2 → 0 dòng |
| Bronze | products 687, interactions 14.748 (mỗi bảng 1 snapshot) |
| Silver | 687 / 14.748 (không có bản trùng) |
| Gold | `item_features` 687, `user_features` 1.419 |
| Feast | `export_to_parquet` 687/1.419; `feast apply` + materialize vào Redis OK |
| Train | `val_ndcg_at_10` 0,0913 vs Pop 0,0444; số đầy đủ ở `model_card.md` |
| Promote | version 1 → alias `production` (chưa có champion) |
| `similar_items` | 687 key Redis |
| Serving | `/health` → `model_version: "1"` (tự nạp qua polling, không restart); `/recommend/homepage` nguồn `model`; `/recommend/similar` nguồn `similar_items` |
| UI | gợi ý hợp lịch sử (user thích PS4 action-RPG nhận God of War, RDR2, AC Odyssey…); bấm mua → danh sách đổi ngay (Spider-Man GOTY, Nioh, Miles Morales lên đầu) |

### 7.2 Quan sát khi test tay

- **Giao diện Lakekeeper không query/preview được bảng** (lỗi "Full download
  failed … 404" rồi "CORS Error"): DuckDB-WASM chạy trong trình duyệt nên cần
  (1) S3 endpoint truy cập được từ trình duyệt cùng URL Lakekeeper dùng
  (`minio:9000` chỉ phân giải trong mạng Docker; thêm `127.0.0.1 minio` vào
  hosts giải quyết phần này), (2) CORS trên MinIO, (3) credential tạm do
  Lakekeeper cấp — `bootstrap.sh` đặt `sts-enabled: false` nên request tới
  bucket private bị `AccessDenied`. Dữ liệu không hỏng: đọc lại bảng bằng
  PyIceberg trong container cho đúng số dòng. Quyết định: không bật STS, xem
  dữ liệu bằng PyIceberg/MinIO Console (file `.gz.metadata.json` và `.avro`
  không preview được là bình thường).
- Cảnh báo `Delete operation did not match any records` ở Silver/Gold lần chạy
  đầu là bình thường (`overwrite` = delete + append trên bảng rỗng).
- Test `/interact` đã ghi 1 dòng thật vào `source-db` và Redis (user
  `AE25ZD…ME4A`, mua Spider-Man): dữ liệu demo hiện lệch seed 1 dòng.

### 7.3 8 dòng fixture không vào Bronze — đã tìm ra nguyên nhân (2026-11-24)

`seed_data.sql` thêm 8 interaction fixture (`id` 14749–14756, `updated_at`
tháng 01/2026, sớm hơn dữ liệu Amazon 2026-11-14) nhưng extract chỉ trả
14.748 dòng, trong khi 5 sản phẩm fixture thì có mặt.

**Nguyên nhân**: state cục bộ cũ của dlt, không phải lỗi lọc của dlt. Thư mục
`data/dlt-pipelines/` là bind mount trên máy host nên `make clean` (chỉ
`down --volumes`) không xoá nó. Lần chạy tay 2026-09-27 để lại
`reco_extract_interactions/state.json` với mốc `updated_at` 2026-09-27. Khi cold
start 2026-11-19, dlt thấy state cục bộ nên không khôi phục từ bucket;
`start_value` của lần load đầu là `2026-09-27T18:05:48` (đọc từ
`_dlt_pipeline_state` đầu tiên trong `extract-staging`), nên truy vấn
`updated_at > mốc` bỏ qua 8 dòng tháng 01/2026. `reco_extract_products` có
thư mục `schemas` tạo lại ngày 2026-11-19 và `start_value` = 1970, nên sản
phẩm fixture vào đủ.

**Kiểm chứng**: chạy `make_source_resource` trên pipeline dlt mới (thư mục
state trống, đích `file://`) với `page_size` 1000 và 100000 đều trả đủ 14.777
dòng, gồm 8 dòng fixture. Source hiện có 14.777 dòng, Bronze 14.769 — đúng 8
dòng chênh.

**Đã sửa**: `make clean` xoá thêm `data/dlt-pipelines/reco_extract_*`; thêm
giải thích vào `docs/runbook.md` mục cold start.

**Không khôi phục 8 dòng trong môi trường hiện tại**: chúng nằm dưới mốc nên
`>` không bao giờ lấy lại (hệ quả đã ghi ở docstring `dlt_writer.py`). Khôi
phục cần xoá state cục bộ lẫn bucket rồi nạp lại toàn bộ interactions, làm
Bronze nhân đôi 14.7k dòng để lấy 8 dòng fixture không dùng cho demo. Lần
cold start sạch kế tiếp sẽ nạp đủ. Không cần ADR vì không lệch tài liệu gốc.

### 7.4 Bổ sung 2026-11-24 — làm tab Model metrics của MLflow có nghĩa

Trước đó 23/25 metric chỉ log 1 giá trị nên MLflow vẽ mỗi cái thành 1 thanh
của 1 run, và không có ảnh nào. Sửa trong `train_agf.py` (không đổi model hay
tên metric `val_*`/`test_*`/`baseline_pop_*` mà `promote.py`/monitoring đọc):

- `best_epoch`, `epochs_run`, `train_seconds`, `train_samples`, `val_samples`,
  `n_params` chuyển từ metric sang **param** (vẫn nằm trong JSON kết quả job).
- Log theo epoch: `train_loss`, `val_ndcg_at_10_epoch`, **mới**
  `val_recall_at_10_epoch`, và baseline Popularity thành đường ngang
  (`baseline_pop_val_{ndcg,recall}_at_10_epoch`).
- 2 ảnh `figures/` trong Artifacts (đường cong huấn luyện kèm đường Pop và
  best epoch; cột AGF so với Pop cho valid/test @10/@20).
- `item_table_max_abs_diff` vẫn là metric (job tự fail nếu ≥ 1e-5).
- Các run cũ (v1–v3) giữ nguyên dạng cũ; chạy từ version 4 trở đi theo dạng mới.
- Test: `tests/model/test_train_agf_smoke.py` kiểm tra params, các step của
  đường cong và 2 ảnh.

### 7.4 Airflow và monitoring với model mới (2026-11-24)

Sau khi sửa lỗi ký URL của Lakekeeper (`LAKEKEEPER__BASE_URI`, ADR 0009), chạy
chuỗi `dag_transform` → `dag_training` trên Airflow xanh; registry có 4 version,
`production` = version 4. `dag_monitoring` chạy xong cả hai task:

| Báo cáo | Kết quả |
|---|---|
| `data_drift_report` | baseline 14.748 dòng (`train.parquet` đã upload lại lên `monitoring-baseline`), hiện tại 14.769 dòng; `current_cold_start_ratio` 0,0001. Dữ liệu gần như không đổi so với baseline nên không kỳ vọng drift. Kết luận drift từng cột nằm ở file HTML (`monitoring-reports/data_drift/`), JSON không ghi |
| `model_quality_report` | `val_ndcg_at_10` theo version: 1 = 0,0913; 2 = 0,0913; 3 = 0,0920; 4 = 0,0920. Vocab coverage 687/687 (1,0) cho version 4 |

Giải thích số liệu: version 1 = 2 và 3 = 4 vì train cố định seed 2023 và không
có dữ liệu mới giữa hai lần train (version 3 khác 1 do `user_features` đã tính
lại). Prometheus: 3 target (`serving`, `redis`, `airflow`) đều `up`; dashboard
Grafana `serving-overview` có request rate, latency p95 (~20 ms cho
`/recommend/homepage`) và cache hit ratio 100% sau 30 request cùng một user;
panel Error rate hiện "No data" vì chưa có phản hồi 5xx nào.

**Kiểm tra bộ phát hiện drift (2026-11-24, chạy tay trong `monitoring-job`, không ghi
báo cáo, không đụng Silver)**: cùng `DataDriftPreset` và baseline `train.parquet`.
Với Silver thật (14.769 dòng): `dataset_drift=False`, cả `rating` và
`is_cold_start_item` không drift (điểm 0,0 và 0,0048). Với lô tổng hợp cùng kích
thước (rating lệch thấp, 60% item lạ): `dataset_drift=True`, cả 2 cột drift
(điểm 0,447 và 0,524). Mới chứng minh bộ phát hiện hoạt động; **chưa** chứng
minh trên lô dữ liệu thật khác phân phối (`test.parquet`/`incoming/`) chạy qua
`dag_monitoring`. Jenkins đã chạy xanh với model AGF (build #7, `phase-8-cicd.md` mục 4).

### 7.5 Cổng promote so cùng split (2026-11-24, ADR 0013)

Sau khi nạp `incoming/`, v5 đạt 0,0847 < 0,0920 của v4 và bị từ chối, nhưng hai số
đo trên hai tập valid khác nhau (1.517 và 1.378 mẫu) và Pop cũng tụt 0,0442 →
0,0373. Sửa: `train_agf` chấm champion trên split của challenger
(`jobs/training/champion_eval.py`), `promote.py` so số đó (`>` chặt).

Chạy thật trên stack (v6 = cùng Gold với v5, champion = v4, `promote --dry-run`):

| Số đo trên split của v6 (1.517 mẫu valid) | `val_ndcg_at_10` |
|---|---|
| v6 (challenger) | 0,0847 |
| v4 (champion, chấm lại) | **0,0680** |
| Pop | 0,0373 |

Lift so với Pop: v6 2,27×, v4 2,08× (số của run v4 trên split cũ). Cổng ra
`comparison: "same_split"`, `would_promote: true`; alias vẫn ở v4 vì chạy
`--dry-run`. Version 5 không có số same-split nên vẫn rơi về `legacy` và bị từ
chối như cũ. Không có mẫu nào champion không chấm được (0 target lạ, 0 chuỗi
rỗng). 1 seed, ~1,5K mẫu valid: hiệu 0,0167 chưa có khoảng tin cậy.

### 7.6 Vòng lặp drift → retrain (2026-11-24, ADR 0014)

`dag_continuous_training` chạy thật từ trạng thái có drift thật (Silver 18.456 dòng
so với baseline `train.parquet` 14.748, `incoming/` đã nạp): quyết định `retrain=true`,
chuỗi ingest → … → training xanh trong ~5 phút, v8 được chấm champion v4 trên cùng
split (0,0680) và **được promote** (0,0847, `same_split`); serving báo
`model_version: 8`. `refresh_baseline` ghi baseline từ snapshot Silver lúc train v8;
lần chạy lại ngay: không drift, `new_rows = 0`, `retrain=false`, các task sau skipped,
run xanh. Bảng đầy đủ ở ADR 0014. Production đã chuyển v4 → v8.

### 7.7 Nhiều ứng viên trong một lần train (2026-11-24, ADR 0015)

`train_agf --candidates gru4rec,sasrec,gru4rec_sum` (hoặc `TRAIN_CANDIDATES`) huấn luyện
từng ứng viên trên cùng split, mỗi ứng viên một run MLflow, chọn một trên valid theo luật
"hơn mặc định và vượt 1 sai số chuẩn của hiệu theo cặp" (`jobs/training/candidates.py`) và
chỉ đăng ký người thắng. Lúc làm (2026-11-24) mặc định là `gru4rec`, một run; từ 2026-11-25 mặc định là ba ứng viên (ADR 0016). Điều kiện của spec đã
thoả trước khi làm: `serving/tests/test_score_candidates.py` có thêm ca `sasrec`,
`fusion=sum`, `id_only`, chấm khớp `item_table @ state`.

Chạy thật (champion = v8): `gru4rec` 0,0847, `sasrec` 0,0844 (z = −0,06), `gru4rec_sum`
0,0703 (z = −2,82) → giữ `gru4rec`, đăng ký version 9, tổng ~6 phút. Cổng promote với
champion v8 cùng cấu hình và dữ liệu cho 0,0847 = 0,0847 → không promote (`>` chặt); điểm
champion chấm lại khớp chính xác điểm của run, một phép kiểm tra thực của ADR 0013. Bảng
đầy đủ ở ADR 0015 và `model_card.md`.

## 6. Danh sách file đã triển khai

### `libs/` (version 0.7.0)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `libs/src/reco_mlops_libs/ranking/agf/__init__.py` | Package AGF | Không import gì (tránh kéo torch) |
| `libs/src/reco_mlops_libs/ranking/agf/agf_core.py` | `AGFItemEncoder`, `AGFSequentialModel` | Chép từ `experiment` `58d50db`; cần torch |
| `libs/src/reco_mlops_libs/ranking/agf/features.py` | Quy tắc thuộc tính + `train_counts` | `MIN_TOKEN_COUNT = MIN_STORE_ITEMS = 5`, 10 bucket giá, MiniLM |
| `libs/src/reco_mlops_libs/ranking/agf/trainer.py` | `MethodConfig`, `train`, `evaluate`, `export` | Patience 10 kiểu RecBole (`>=`) |
| `libs/src/reco_mlops_libs/ranking/base.py` | Thêm `REGISTERED_MODEL_NAME` | `"reco-mlops-agf"` |
| `libs/src/reco_mlops_libs/mlflow_utils/logging_helpers.py` | Thêm `log_iceberg_snapshots()` | Param `iceberg_snapshot_id.<bảng>` |
| `libs/src/reco_mlops_libs/iceberg/schema_contracts/{bronze_products,silver_products,gold_item_features}.py` | Cột `category_path` | Field id 13 / 11 / 16, nullable |

### Dữ liệu & SQL

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `infra/source-db/init/schema.sql` | Cột `products.category_path` | `TEXT` nullable |
| `scripts/seed_source_db.py` | Sinh `category_path` khi seed | `CATEGORY_PATH_SEPARATOR = " > "` |
| `sql/silver/silver_products.sql` | Mang `category_path` sang Silver | — |
| `sql/gold/gold_item_features.sql` | Mang `category_path` sang Gold | Thêm vào `GROUP BY` |

### `jobs/` (image `training-job`, trừ monitoring)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/training/train_agf.py` | Job train AGF + log MLflow | `EXPERIMENT_NAME = REGISTERED_MODEL_NAME`; CLI `--no-text/--epochs/--seed`; fail nếu lệch `H` ≥ 1e-5 |
| `jobs/training/agf_inputs.py` | Gold → đầu vào AGF (thuần pandas) | `CATEGORY_PATH_SEPARATOR = " > "` |
| `jobs/training/baselines.py` | Popularity cùng split | Hoà điểm tách theo chỉ số |
| `jobs/training/training_figures.py` | 2 ảnh log vào run (`figures/training_curves.png`, `figures/agf_vs_popularity.png`) | matplotlib backend Agg (đã có sẵn qua `mlflow`) |
| `jobs/training/agf_method_config.yaml` | Cấu hình đã chốt | gru4rec, d 64, L 10, dropout 0,2 |
| `jobs/training/promote.py` | Mặc định registry mới; so cùng split (ADR 0013) | `--model-name` cho registry cũ; `--min-delta`, `--dry-run` |
| `jobs/training/champion_eval.py` | Chấm champion trên split của challenger | Log `champion_*_same_split`, `champion_version` |
| `jobs/training/train_sequence.py` | GRU4Rec cũ (legacy) | Registry `reco-mlops-gru4rec` |
| `jobs/candidates/similar_items.py` | Cosine trên `H` | Artifact `agf/item_table.npy` |
| `jobs/monitoring/model_quality_report.py` | Tên registry từ libs | — |

### Serving

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `serving/app/core/model_loader.py` | Tải model + `H`, `score_candidates`, `ServingRanker` | `ITEM_TABLE_ARTIFACT = "agf/item_table.npy"` |

### Hạ tầng, orchestration & test

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `infra/docker/training/Dockerfile` | Cài `training-pypi.txt`, CMD mới | `CMD ["jobs.training.train_agf"]` |
| `requirements/training-pypi.txt` | Dependency PyPI của image training | `sentence-transformers==3.4.1`, `redis==5.2.1` |
| `airflow/dags/dag_training.py` | `train_agf >> promote >> similar_items` | task id `train_agf` |
| `Makefile` | `train` → AGF, thêm `train-gru4rec` | — |
| `tests/unit/training/test_agf_inputs.py` | Unit test ánh xạ dữ liệu | Không cần torch |
| `tests/unit/training/test_baselines.py` | Unit test popularity | Không cần torch |
| `tests/model/test_train_agf_smoke.py` | Smoke test train AGF | `use_text=False`, 2 epoch, `file://` MLflow |
| `serving/tests/test_score_candidates.py` | Serving khớp full ranking | Model tí hon d = 8 |
| `serving/tests/test_model_reload.py` | Reload version không restart | Train 2 version AGF |
