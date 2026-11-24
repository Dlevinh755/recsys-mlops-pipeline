# Trạng thái triển khai

> Cập nhật mỗi khi một phase chuyển trạng thái. Xem quy ước cập nhật trong
> `CLAUDE.md` mục "Quy ước bắt buộc khi hoàn thành một phase".

Cập nhật lần cuối: 2026-11-24 (Jenkins chạy thật — ADR 0011; Silver tăng dần — ADR 0010; ghim s3fs 2026.1.0 — ADR 0009)

| Phase | Nội dung | Trạng thái | Tài liệu as-built |
|---|---|---|---|
| 0 | Khung project & hạ tầng nền tảng | ✅ Hoàn thành | [phase-0-ha-tang-nen.md](modules/phase-0-ha-tang-nen.md) |
| 1 | Extract & cơ chế watermark | ✅ Hoàn thành | [phase-1-extract-watermark.md](modules/phase-1-extract-watermark.md) |
| 2 | Lakehouse Bronze → Silver → Gold (Iceberg) | ✅ Hoàn thành | [phase-2-lakehouse.md](modules/phase-2-lakehouse.md) |
| 3 | Feature Store (Feast + Redis) — thiết kế theo hướng chuỗi (ADR 0004) | ✅ Hoàn thành | [phase-3-feature-store.md](modules/phase-3-feature-store.md) |
| 4 | Training & Model Registry — **GRU4Rec, không phải LightGBM** (ADR 0004) | ✅ Hoàn thành | [phase-4-training.md](modules/phase-4-training.md) |
| 5 | Serving (FastAPI) + UI (Streamlit) | ✅ Hoàn thành | [phase-5-serving.md](modules/phase-5-serving.md) |
| 6 | Orchestration (Airflow) | ✅ Hoàn thành | [phase-6-orchestration.md](modules/phase-6-orchestration.md) |
| 7 | Monitoring (Prometheus/Grafana + Evidently) | ✅ Hoàn thành | [phase-7-monitoring.md](modules/phase-7-monitoring.md) |
| 8 | CI/CD, test, tài liệu, đóng gói `libs/` | ✅ Hoàn thành | [phase-8-cicd.md](modules/phase-8-cicd.md) |
| 9 | ~~Sequence Model (mở rộng, tùy chọn)~~ — gộp vào Phase 4 theo ADR 0004 | ⬜ N/A | — |
| 10 | AGF-GRU4Rec thay GRU4Rec chỉ-ID + cột `category_path` (ADR 0008) | 🟡 Đã chạy thật end-to-end (2026-11-19), 26 test pass trong image training; 8 dòng fixture: đã rõ nguyên nhân (7.3) | [phase-10-agf.md](modules/phase-10-agf.md) |

## Trạng thái tổng thể

**2026-11-24 — Sửa Jenkins đợt 2 (ADR 0011 mục 5–7), đã push, build #10 và #11 xanh (6,8 phút khi cache ấm):** `Build & Push`
chỉ tag lại image tag SHA đã test (trước đó push nhầm bản cũ cho 3 image),
Model Smoke Test `--no-deps` + `down` ở `post.always` (build #7/#8 bỏ sót ~9
container mỗi lần, làm build #9 fail), Jenkins có buildx. Ghim `fastapi`/`uvicorn`
trong `serving.txt`. Airflow đã đọc `IMAGE_TAG` từ Phase 8 (kiểm chứng task chạy
từ image `:7156423`), mô tả "latest" cũ chỉ là tài liệu lỗi thời.

**2026-11-24 — Bảo trì Iceberg (ADR 0012):** `compact_iceberg` compact thêm Silver,
job mới `expire_snapshots` gỡ snapshot quá 7 ngày và xoá file mà snapshot đó
dùng riêng (PyIceberg 0.12 chỉ sửa metadata, đã đo). 6 integration test pass,
kiểm tra đột biến bắt được lỗi xoá nhầm file đang dùng. Chưa xoá thật trên bảng
thật vì chưa snapshot nào đủ cũ; `pollSCM` đã được xác nhận (build #8 tự chạy,
xanh).

**2026-11-24 — Jenkins chạy thật (ADR 0011):** build #7 (commit `87b0837`) xanh
cả 6 stage trong khoảng 16,7 phút, gồm 17 integration test, skew test, 2 model
smoke test (AGF) và đẩy 8 image lên Docker Hub tag `87b0837`. Sáu build trước
lộ bốn chỗ lệch thiết kế (chạy `root`, `COMPOSE_FILE` cho mọi stage, workspace
cố định, thiếu bước extract) — đã sửa và ghi ở ADR 0011. Cùng ngày: nguyên nhân
8 dòng fixture không vào Bronze là state dlt cục bộ cũ (`make clean` đã sửa,
`phase-10-agf.md` mục 7.3); kiểm tra bộ phát hiện drift của Evidently trên lô
tổng hợp (7.4).

**2026-11-24 — Silver tăng dần (ADR 0010, không đổi trạng thái phase nào):**
`build_silver` chỉ xử lý Bronze nạp sau watermark `_ingested_at` lưu trong
properties của bảng Silver rồi upsert; cờ `--full-refresh` tính lại toàn bộ.
Đã chạy thật: lần 2 không đổi gì (0 dòng, không snapshot mới), thêm 1 tương
tác qua `/interact` → Silver tăng đúng 1 dòng, `dag_transform` xanh. Gold vẫn
tính lại toàn bộ. Chi tiết: `modules/phase-2-lakehouse.md` mục 2.9.

**Phase 10 (2026-11-14)** — model chính đổi sang **AGF-GRU4Rec** (repo thực
nghiệm `../experiment/`, ADR 0008): `libs` 0.7.0 thêm `ranking/agf/`; job
`train_agf` (L = 10, text MiniLM, baseline popularity cùng split); serving
chấm bằng bảng `H` tính trước; cột `category_path` mới từ `source-db` tới
`gold.item_features`. **2026-11-19**: đã cold start với bộ `Video_Games`, chạy tay
extract → Bronze → Silver → Gold → user_features → Feast → train → promote →
similar_items, serving nạp model v1 và UI gợi ý đúng (AGF `val_ndcg_at_10`
0,0913 vs Pop 0,0444). Kết quả chi tiết: `phase-10-agf.md` mục 7,
`model_card.md`. **2026-11-24**: Airflow (`dag_transform`, `dag_training`) và monitoring
(Prometheus/Grafana, `dag_monitoring`) đã chạy với model mới — `phase-10-agf.md`
mục 7.4. **Việc còn lại**: demo drift thật bằng lô dữ liệu khác phân phối
qua `dag_monitoring`.


**Toàn bộ MVP1 (Phase 0–8) đã hoàn thành.** Không còn phase nào "kế tiếp"
theo `de-xuat-trien-khai.md` — Phase 9 (sequence model) đã gộp vào Phase 4
từ ADR 0004. Việc còn lại từ đây là vận hành/mở rộng, không phải code mới
bắt buộc — xem "Việc còn để lại" ở cuối mỗi `docs/modules/phase-N-*.md`.

Phase 8 (CI/CD, chi tiết ở [phase-8-cicd.md](modules/phase-8-cicd.md)) —
`git init` lần đầu tiên cho project (đã là gap có sẵn từ Phase 0, chặn cả
tag ảnh theo git SHA lẫn CI/CD thật — nay đã giải quyết), `Jenkinsfile` 6
stage (ADR 0005) đã validate qua chính API linter của Jenkins, mọi stage đã
chạy thật thủ công trên stack cô lập (`reco-ci-test`, không đụng stack dev)
và pass: 16 unit test (tách 3 image), 5 integration test, lint sạch,
model-smoke-test (fixture fixed-seed, không cần Iceberg/MLflow server), 2
test mới — train/serve skew (Gold vs Feast/Redis thật) và model-reload
(train 2 version thật, flip alias `production`, xác nhận `serving` tự nhận
version mới không cần restart). 4 tài liệu mới: `architecture.md`,
`runbook.md` (có quy trình rollback model), `data_dictionary.md`,
`model_card.md`. Đã chạy thật qua Jenkins từ 2026-11-24 (xem đầu mục này
và ADR 0011); `pollSCM` đã xác nhận ở build #8.

## Việc còn lại để hoàn thiện project (rà soát 2026-10-11)

Không còn phase bắt buộc; các điểm dưới đây là hạn chế đã xác nhận qua rà
soát, theo thứ tự ưu tiên đề xuất (chưa làm, chưa có code):

1. ~~Airflow vẫn chạy image tag `latest`~~ — mô tả này đã lỗi thời từ Phase 8:
   `JobDockerOperator` đọc tên image từ `docker compose config` nên theo
   `IMAGE_TAG` trong `.env`. Đã kiểm chứng 2026-11-24 (task chạy từ image
   `:7156423`); cách dùng ở `runbook.md` mục "Chạy DAG bằng image của CI".
2. ~~CI/CD chưa chạy thật qua Jenkins~~ — đã chạy xanh 2026-11-24 (ADR 0011);
   `GIT_SHA` đã xác nhận. `pollSCM` đã xác nhận bằng build #8 (tự chạy, xanh).
3. ~~Chất lượng model thấp (NDCG@10 ≈ 0.008), chưa có baseline~~ → Phase 10
   (AGF + baseline popularity log cùng run); số đo thật trên Video_Games:
   `val_ndcg_at_10` 0,0913–0,0920 so với Pop 0,0444 (`model_card.md`).
   `test.parquet`/`incoming/` vẫn chưa nạp.
4. **Vòng lặp drift → retrain chưa khép kín**: mọi DAG `schedule=None`,
   Evidently không kích hoạt `dag_training`, `/interact` chỉ ghi hành động
   "mua" (chưa có impression/click để tính online metric).
5. **Chưa có Alertmanager.** ~~`expire_snapshots`~~ đã làm (ADR 0012); chưa chạy xoá thật vì chưa snapshot nào quá 7 ngày.
6. ~~`fastapi`/`uvicorn` chưa ghim~~ — đã ghim 2026-11-24 (`fastapi==0.163.2`,
   `uvicorn[standard]==0.34.0`, ADR 0007); `redis` cố ý không ghim vì feast cap `<5`.

## Lịch sử các phase trước Phase 8

## Ghi chú kiểm thử thủ công Phase 0→3 (2026-09-27)

Trước khi bắt đầu Phase 4, chạy lại thủ công toàn bộ pipeline Phase 0→3 từ
cold start và phát hiện 1 bug mất dữ liệu thật ở Phase 1 (watermark tiến
theo batch nội bộ nhưng Phase 2 chỉ nạp Bronze từ run Postgres có
`status='succeeded'` — một run bị crash giữa chừng khiến ~13.000/16.230
dòng `interactions` bị kẹt vĩnh viễn, không nạp được vào Bronze). Đã sửa tận
gốc bằng cách chuyển Phase 1 sang dùng `dlt.sources.incremental` thay vì
watermark tự viết trong Postgres — xem
[ADR 0006](decisions/0006-dlt-native-incremental-extract.md),
`docs/modules/phase-1-extract-watermark.md` mục 8 và
`docs/modules/phase-2-lakehouse.md` mục 4.3. Đã kiểm chứng lại: Bronze đạt
đủ 1565/1565 products và 16230/16230 interactions.

## ADR đã ghi nhận

Xem `docs/decisions/` — đánh số tăng dần, mỗi file là một quyết định lệch
khỏi `de-xuat-trien-khai.md`/`bao-cao-ky-thuat.md`:

- [0001](decisions/0001-lakekeeper-thay-rest-catalog-tu-viet.md) — Dùng
  Lakekeeper thay vì tự cấu hình REST Catalog
- [0002](decisions/0002-mlflow-som-o-phase-0.md) — Dựng hạ tầng MLflow ngay
  ở Phase 0 thay vì Phase 4
- [0003](decisions/0003-extract-writes-directly-to-minio.md) — Extract ghi
  thẳng lên MinIO thay vì Parquet cục bộ
- [0004](decisions/0004-gru-sequence-model-thay-lightgbm.md) — GRU (sequence
  model) thay LightGBM làm model chính; Phase 9 gộp vào Phase 4
- [0005](decisions/0005-jenkins-thay-github-actions.md) — Jenkins thay
  GitHub Actions cho CI/CD (Phase 8, đã áp dụng)
- [0006](decisions/0006-dlt-native-incremental-extract.md) — dlt sở hữu
  cursor + state của extract (Phase 1), thay watermark tự viết trong
  Postgres — sửa bug mất dữ liệu phát hiện khi test thủ công 2026-09-27
- [0007](decisions/0007-downgrade-feast-for-mlflow-compat.md) — Hạ
  `feast` 0.66.0 → 0.55.0 (Phase 5) — xung đột dependency thật với
  `mlflow-skinny` khi `serving` cần dùng cả 2 cùng lúc
- [0008](decisions/0008-agf-gru4rec-thay-gru4rec.md) — AGF-GRU4Rec thay
  GRU4Rec chỉ-ID làm model chính (chọn ở repo thực nghiệm), thêm cột
  `category_path` (Phase 10)
- [0009](decisions/0009-pin-s3fs-remote-signing.md) — Ghim `s3fs`/`fsspec`
  2026.1.0 cho image dùng PyIceberg + remote signing (Lakekeeper)
- [0010](decisions/0010-silver-incremental.md) — Silver tăng dần (watermark
  `_ingested_at` + upsert) thay cho tính lại toàn bộ
- [0011](decisions/0011-jenkins-chay-that-dieu-chinh.md) — Điều chỉnh Jenkins
  khi chạy thật: `root`, `COMPOSE_FILE` mọi stage, workspace cố định, bước extract
- [0012](decisions/0012-iceberg-maintenance-expire-snapshots.md) — Bảo trì Iceberg:
  compact Silver và `expire_snapshots` có xoá file
