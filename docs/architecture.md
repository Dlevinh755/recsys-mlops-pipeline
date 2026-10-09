# Kiến trúc hệ thống

> Bản tóm tắt để tra cứu nhanh (Phase 8). Lý do chọn từng công nghệ đã có
> đầy đủ ở `bao-cao-ky-thuat.md`; sơ đồ thư mục đầy đủ ở `cau-truc-project.md`;
> nguyên tắc bắt buộc khi sinh code mới ở `CLAUDE.md`. File này không lặp
> lại các lý do đó, chỉ vẽ lại bức tranh tổng thể của hệ thống **thực tế đã
> chạy được** tính đến Phase 8.

## Sơ đồ luồng dữ liệu

```
                     ┌─────────────┐
                     │  source-db  │  Postgres — OLTP giả lập
                     │ (Postgres)  │  (users/products/interactions)
                     └──────┬──────┘
                            │ dlt incremental (Phase 1)
                            ▼
                     extract-job ──► MinIO (extract-staging, Parquet)
                            │
                            ▼ (Phase 2, PyIceberg + Lakekeeper REST Catalog)
                     transform-job
                     ┌─────────────────────────────┐
                     │ Bronze (append) → Silver     │
                     │ (dedup) → Gold (aggregate)   │  Iceberg / MinIO
                     └──────┬──────────────┬────────┘
                            │              │
              (Phase 3)     ▼              ▼ (Phase 4)
        materialize-job → Redis    training-job (GRU4Rec, PyTorch)
        (Feast online store)              │
                            │              ▼
                            │         MLflow Model Registry
                            │         (alias `production`)
                            │              │
                            ▼              ▼ (poll, Phase 5)
                     ┌──────────────────────────┐
                     │        serving (FastAPI)  │──► ui (Streamlit)
                     │  homepage/similar/interact│
                     └──────┬───────────────────┘
                            │ POST /interact (ngoại lệ kiến trúc duy nhất —
                            │ ghi thẳng Redis + source-db, xem mục dưới)
                            ▼
                     source-db.interactions (khớp lại vòng lặp)

   Airflow (Phase 6) ──gọi lại──► extract/transform/materialize/training/
   (JobDockerOperator)             monitoring-job (không business logic mới)

   Prometheus/Grafana (Phase 7, vận hành) ◄── /metrics (serving) + exporter
   Evidently (Phase 7, dữ liệu/mô hình) ──► report HTML/JSON trên MinIO

   Jenkins (Phase 8) ──poll SCM──► Lint/Test/Model-smoke-test/Build&Push
```

## Nguyên tắc kiến trúc cốt lõi

Xem đầy đủ ở `CLAUDE.md`/`bao-cao-ky-thuat.md` mục 10.1 — tóm tắt 3 điểm hay
bị hỏi nhất:

1. **Iceberg là nguồn sự thật duy nhất** giữa các tầng batch (extract →
   transform → feature → training). Không tầng nào gọi thẳng API nội bộ của
   tầng khác.
2. **Ngoại lệ kiến trúc duy nhất đã ghi nhận**: `POST /interact` (`serving`)
   ghi thẳng Redis (tín hiệu tức thời cho model, Hướng B — ADR 0004) và
   `source-db` (để lần extract kế tiếp khớp lại) — không đi qua Iceberg vì
   cần độ trễ ~0 cho demo "mua xong thấy gợi ý đổi ngay". Không thêm ngoại
   lệ tương tự nếu chưa có ADR.
3. **Mỗi nhóm job = 1 Docker image**, orchestration (Airflow/Jenkins) chỉ
   gọi lại `python -m jobs.xxx.run` đã test độc lập — không nhúng business
   logic vào DAG/Jenkinsfile.

## Danh sách image (Phase 8, ADR 0005)

| Image | Business logic tự viết | Qua Jenkins Build & Push |
|---|---|---|
| `extract` | `jobs/extract/` | Có |
| `transform` | `jobs/transform/`, `jobs/features/`, `jobs/maintenance/` | Có (tag lại, không build lại) |
| `materialize` | `jobs/materialize/` | Có (tag lại, không build lại) |
| `training` | `jobs/training/`, `jobs/candidates/` | Có (tag lại, không build lại) |
| `serving` | `serving/app/` | Có |
| `airflow` | `airflow/dags/`, `airflow/plugins/` | Có |
| `monitoring` | `jobs/monitoring/` | Có |
| `ui` | `ui/app.py` | Có |
| `mlflow` | Không (wrapper mỏng quanh image upstream) | Không — build local |
| `jenkins` | Không (wrapper mỏng quanh image upstream) | Không — build local (tránh nghịch lý CI tự build lại image chạy chính nó) |

Chi tiết đầy đủ (tiêu chí phân nhóm, layer caching, multi-stage...) ở
`PHONG-VAN-QA-BAO-VE.md` mục 2.

## Tài liệu liên quan

- `de-xuat-trien-khai.md` — checklist gốc từng phase.
- `docs/modules/phase-N-*.md` — thực tế đã code ("as-built"), nguồn sự thật
  khi lệch với checklist gốc.
- `docs/decisions/` — ADR cho mọi lựa chọn lệch tài liệu gốc.
- `docs/runbook.md` — vận hành hằng ngày, xử lý sự cố, rollback model.
- `docs/data_dictionary.md` — schema từng bảng.
- `docs/model_card.md` — GRU4Rec: dữ liệu train, metric, giới hạn.
