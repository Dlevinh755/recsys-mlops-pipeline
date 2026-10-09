# Data dictionary

> Nguồn sự thật cho từng bảng là schema contract trong
> `libs/src/reco_mlops_libs/iceberg/schema_contracts/`
> (`ICEBERG_SCHEMA` + `PANDERA_SCHEMA`) — file này là bản tóm tắt để tra cứu
> nhanh, không phải bản khai báo. Khi 2 nguồn lệch nhau, tin schema contract.

## Source-db (Postgres, OLTP)

`infra/source-db/init/schema.sql`.

### `users`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| `user_id` | `TEXT PK` | |
| `display_name` | `TEXT` | nullable |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | |

### `products`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| `product_id` | `TEXT PK` | |
| `title` | `TEXT NOT NULL` | |
| `category` | `TEXT NOT NULL` | |
| `brand` | `TEXT` | nullable |
| `price` | `NUMERIC(12,2)` | `>= 0` |
| `description` | `TEXT` | **luôn NULL** trong dataset Amazon seed (không có mô tả text thật) |
| `image_url` | `TEXT` | thêm sau (xem `amazone_data/DATA.md`), nullable |
| `is_active` | `BOOLEAN` | default `TRUE` |

### `interactions`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| `interaction_id` | `BIGINT PK` (identity) | |
| `user_id`/`product_id` | `TEXT FK` | |
| `event_type` | `TEXT` | `view`/`cart`/`purchase`/`rating` |
| `rating` | `NUMERIC(2,1)` | bắt buộc nếu `event_type='rating'`, cấm nếu ngược lại (CHECK) |
| `event_time` | `TIMESTAMPTZ` | dùng làm cursor watermark cùng `interaction_id` |

## Lakehouse (Iceberg, qua Lakekeeper REST Catalog)

### `bronze.products` / `bronze.interactions`
Gần như nguyên trạng nguồn + 2 cột lineage: `_extraction_run_id` (dlt load
id, ADR 0006), `_ingested_at`. Append-only, idempotent theo
`_extraction_run_id`.

### `silver.products` / `silver.interactions`
Dedup theo khoá chính (`product_id`/`interaction_id`), bỏ cột lineage.
`overwrite()` toàn bộ mỗi lần chạy (không incremental).

### `gold.item_features`
Một dòng/`product_id`: `title`, `category`, `brand`, `price`, `image_url`,
`num_interactions`/`num_views`/`num_cart_adds`/`num_purchases`/`num_ratings`,
`avg_rating`, `distinct_users`, `last_interaction_at`, `computed_at`.

### `gold.user_features`
Một dòng/`user_id`: `item_sequence` (tối đa 10 item gần nhất, cũ→mới),
`event_time_sequence` tương ứng, `sequence_length`, `last_event_time`,
`computed_at`. Input chính cho GRU4Rec (không phải bảng đặc trưng phẳng —
xem ADR 0004).

## Online feature store (Feast + Redis)

`feature_repo/features_user.py` — `user_recent_items` FeatureView, entity
`user`, field `item_sequence`/`event_time_sequence`/`sequence_length`. Ghi
qua `jobs/materialize/feast_materialize.py` (Phase 3), đọc qua
`serving/app/core/sequence_source.py` (Phase 5).

Key Redis khác (không qua Feast, namespace riêng):
- `recent_items:{user_id}` — phiên tương tác tức thời (`POST /interact`).
- `similar_items:{product_id}` — top-K sản phẩm tương tự (cosine similarity
  trên embedding GRU4Rec, `jobs/candidates/similar_items.py`).
- `cache:homepage:{user_id}` / `cache:similar:{item_id}` — cache response.

## MLflow

- Experiment: `reco-mlops-gru4rec`.
- Registered model: `reco-mlops-gru4rec`, alias `production` (không dùng
  stage API cũ, xem `docs/modules/phase-4-training.md`).
- Artifact mỗi run: `model` (PyTorch, `mlflow.pytorch.log_model`),
  `item_vocab.json` (dict `product_id -> index`).
- Metric chính: `val_ndcg_at_10`/`val_recall_at_10`/`val_map_at_10`/
  `val_coverage_at_10` (gate promote: `val_ndcg_at_10`).

## Monitoring (MinIO)

Bucket `monitoring-baseline` (snapshot `train.parquet`, upload 1 lần qua
`scripts/upload_monitoring_baseline.py`), `monitoring-reports` (HTML/JSON
từ `jobs/monitoring/`, key `{job_name}/{timestamp}.{html,json}`).
