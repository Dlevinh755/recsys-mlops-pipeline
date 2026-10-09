# Trạng thái triển khai

> Cập nhật mỗi khi một phase chuyển trạng thái. Xem quy ước cập nhật trong
> `CLAUDE.md` mục "Quy ước bắt buộc khi hoàn thành một phase".

Cập nhật lần cuối: 2026-09-30 (Phase 8)

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

## Trạng thái tổng thể

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
`model_card.md`. Việc còn thuần thao tác tay (không phải code): tạo repo
GitHub + Docker Hub token thật, tạo job Multibranch Pipeline trỏ vào —
`Build & Push Images` sẽ fail rõ (không chặn pipeline, `catchError`) cho
tới lúc đó — xem `phase-8-cicd.md` mục 5.

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
  GitHub Actions cho CI/CD (Phase 8, chưa code)
- [0006](decisions/0006-dlt-native-incremental-extract.md) — dlt sở hữu
  cursor + state của extract (Phase 1), thay watermark tự viết trong
  Postgres — sửa bug mất dữ liệu phát hiện khi test thủ công 2026-09-27
- [0007](decisions/0007-downgrade-feast-for-mlflow-compat.md) — Hạ
  `feast` 0.66.0 → 0.55.0 (Phase 5) — xung đột dependency thật với
  `mlflow-skinny` khi `serving` cần dùng cả 2 cùng lúc
