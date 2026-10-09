# Phase 5 — Serving (FastAPI) + UI (Streamlit)

> Trạng thái: **Hoàn thành**. Đối chiếu với `de-xuat-trien-khai.md` mục
> "Phase 5" (viết cho model LightGBM, đọc cùng
> [ADR 0004](../decisions/0004-gru-sequence-model-thay-lightgbm.md) — model
> thật là GRU4Rec, interface `RankingModel` không đổi). Phụ thuộc: Phase 3
> (Feast/Redis), Phase 4 (model + registry client).

Thư mục code: `serving/`, `ui/`,
`jobs/candidates/`.

---

## 1. Mục tiêu ban đầu

API trả gợi ý thật, có cache, có fallback, không bao giờ trả 5xx vì thiếu
model/feature. `POST /interact` ghi tín hiệu tương tác tức thời (mục 8.1
báo cáo kỹ thuật), re-rank hậu kỳ bằng sản phẩm tương tự. UI Streamlit demo
được vòng lặp "bấm mua → gợi ý cập nhật".

## 2. Những gì đã triển khai

### 2.1 Quyết định đã chốt trước khi code: vai trò `recent_items` (Hướng B)

ADR 0004 cố tình để ngỏ câu hỏi này khi chuyển model chính sang GRU (nhận
chuỗi làm input trực tiếp, khác LightGBM chỉ dùng chuỗi để boost hậu kỳ).
Đã chốt với người dùng: **Hướng B** — chuỗi dùng để chấm điểm = chuỗi đã
materialize qua Feast (`gold.user_features`, có thể trễ theo chu kỳ batch)
**nối thêm** item vừa tương tác (Redis `recent_items:{user_id}`, tức thời),
cắt còn tối đa 10 phần tử gần nhất (`serving/app/core/sequence_source.py::
merge_sequence()`). **Vẫn giữ** `recency_boost.py` (đẩy sản phẩm tương tự
lên đầu) làm lớp bổ sung — Hướng B khiến model tự phản ánh hành vi mới một
cách ngầm định (khó thấy khi demo), boost hậu kỳ đảm bảo hiệu ứng rõ ràng,
trực quan. Kiểm chứng thật (mục 4) cho thấy 2 cơ chế phối hợp tốt: sau
`POST /interact`, toàn bộ top-10 trả về đều là sản phẩm tương tự item vừa
tương tác.

### 2.2 `libs/src/reco_mlops_libs/ranking/gru4rec.py` — di chuyển kiến trúc GRU4Rec

`GRU4RecNet` (+ `PAD_INDEX`) chuyển từ `jobs/training/train_sequence.py`
sang `libs/`. Lý do bắt buộc: MLflow lưu model bằng pickle — `serving`
(image khác, không cài `jobs/training/` theo CLAUDE.md #2) cần import được
đúng class ở đúng module path để `mlflow.pytorch.load_model()` hoạt động.
`torch` **không** thêm vào `libs/pyproject.toml` (giữ `libs` nhẹ cho
`extract-job`/`transform-job`/`materialize-job`) — chỉ `training-job` và
`serving` tự khai `torch` trong `requirements/*.txt` riêng.

**Hệ quả bắt buộc đã thực hiện**: 2 model version train ở Phase 4 (pickle
theo path cũ `jobs.training.train_sequence.GRU4RecNet`) không load lại
được sau khi chuyển. Đã train + promote lại (version 3, thủ công set alias
vì gate champion/challenger so với version 1 cũ — nay không load được nữa
— sẽ luôn "thua" theo metric ngẫu nhiên; xem mục 4).

### 2.3 `jobs/candidates/similar_items.py` (chạy trong `training-job`)

Tính cosine similarity trên chính `item_embedding` đã train của GRU4Rec
(không cần model/dữ liệu riêng), lấy top-K (mặc định 10) mỗi item, ghi
`similar_items:{product_id}` vào Redis (TTL 7 ngày). Vocab dùng đúng
`item_vocab.json` của run đã promote (không tính lại từ catalog hiện tại —
tránh lệch index nếu catalog đổi từ lúc train). Gộp bước tính + ghi Redis
thành 1 file, chạy trong image `training-job` có sẵn — không dựng thêm
Docker image mới (xem mục 3, quyết định thu hẹp phạm vi).

### 2.4 `serving/` — FastAPI

- `core/config.py` — đọc env qua `reco_mlops_libs.common.env` (tái dùng).
- `core/model_loader.py` — `ModelLoader` poll `registry_client.
  get_production_version()` mỗi `MODEL_POLL_INTERVAL_SECONDS` (mặc định
  60s, chạy bằng `asyncio.create_task` từ FastAPI `lifespan`), cache
  `(net, vocab, version)` trong bộ nhớ — đóng vòng train→promote→serve mà
  không cần restart (mục 7.3 báo cáo kỹ thuật). `ServingRanker(RankingModel)`
  implement `predict()` bằng chuỗi lấy từ `sequence_source.py` (Hướng B) —
  khác `jobs/training/train_sequence.py::GRU4RecRanker` (dùng để tự kiểm
  tra lúc train, scan thẳng Iceberg) ở đúng 1 điểm: nguồn chuỗi.
- `core/sequence_source.py` — `merge_sequence()`/`to_indices()` (hàm thuần)
  + `fetch_historical_sequence()` (Feast)/`fetch_recent_items()` (Redis).
- `core/fallback.py` — `FallbackCatalog` nạp toàn bộ `gold.item_features`
  lúc startup + refresh định kỳ (`FALLBACK_REFRESH_INTERVAL_SECONDS`, mặc
  định 300s), `rank_items_by_popularity()` (hàm thuần) sắp theo
  `num_purchases`/`num_views`. Dùng vừa làm candidate pool cho model, vừa
  làm fallback.
- `core/cache.py` — Redis cache `cache:homepage:{user_id}` (TTL ngắn, mặc
  định 60s)/`cache:similar:{item_id}` (TTL dài hơn, ×5).
- `core/recency_boost.py` — `boost()` (hàm thuần) đẩy sản phẩm tương tự
  item vừa tương tác lên đầu danh sách đã chấm điểm **đầy đủ** (trước khi
  cắt top-N — đảm bảo sản phẩm boost luôn có mặt để đẩy lên, không bị cắt
  mất trước) + `fetch_similar_items()` (Redis).
- `core/source_db_writer.py` — ghi `interactions` (psycopg), validate
  `user_id`/`product_id` tồn tại trước, raise `UnknownEntityError` → API
  trả 404 thay vì lỗi FK Postgres thô.
- `core/metrics.py` — xem mục 3 (đổi khỏi `prometheus-fastapi-
  instrumentator` do xung đột dependency).
- `api/homepage.py`, `api/similar.py`, `api/interact.py` — 3 route, logic
  chi tiết ở mục 2.1/2.4 trên. `interact.py` xoá cache homepage của user
  ngay sau khi ghi — đảm bảo lần gọi lại thấy thay đổi ngay, không đợi hết
  TTL.
- `schemas/models.py` — pydantic, có validator `_rating_matches_event_type`
  khớp đúng CHECK constraint của bảng `interactions`.
- `app/main.py` — `lifespan` khởi động `FallbackCatalog`/`ModelLoader`,
  wiring 3 router + `/health` + `/metrics`.

### 2.5 `ui/app.py` — Streamlit

Gọi HTTP thuần (`requests`) vào `serving` qua `SERVING_BASE_URL`. Nút "Đã
mua" gọi `POST /interact` rồi `st.rerun()` — render lại danh sách ngay
trong cùng lượt bấm, không thao tác gì thêm (đúng Definition of Done).
Không chia sẻ code/image với `serving/` — xoá được hoàn toàn mà không ảnh
hưởng pipeline nghiệp vụ.

Giao diện (làm lại 2026-09-28): `layout="wide"`, sidebar chọn user + badge
nguồn gợi ý (`model`/`fallback`), lưới 4 cột thẻ sản phẩm (ảnh khung vuông
đồng nhất, title cắt 2 dòng, danh mục + giá tách từ `description` bằng
`split_description()`), theme ở `ui/.streamlit/config.toml` (Dockerfile
COPY thêm thư mục này). Dữ liệu chèn vào HTML đều qua `html.escape()`.

### 2.6 Ảnh sản phẩm trên UI (bổ sung 2026-09-28)

`RecommendedItem.image_url` (nullable) — nạp cùng `FallbackCatalog._load_once()`
(quét thêm field `image_url` từ `gold.item_features`, không query Iceberg
riêng cho từng request), gắn vào cả 3 nhánh của `homepage.py` (cache
miss+model, cache miss+cold-start fallback, all-neutral fallback).
`ui/app.py` render bằng `st.image()` nếu có URL. Nguồn dữ liệu: xem
[`phase-2-lakehouse.md` mục 4.4](phase-2-lakehouse.md#44-thêm-image_url-vào-productsitem_features-2026-09-28).
Không đổi `SimilarResponse`/`/recommend/similar` — UI hiện tại không gọi
endpoint đó.

Cùng đợt: `RecommendedItem` thêm `title` + `description` (`gold.item_features`
thêm cột `title`, libs 0.6.0). **`description` là chuỗi tự ghép
`category · $price`** (`fallback.build_item_metadata()`), không phải mô tả
thật — cột `products.description` luôn NULL vì dữ liệu Amazon seed không có
mô tả text. Thêm `GET /users` (`api/users.py`, `core/user_catalog.py`, nạp từ
`gold.user_features`, cùng pattern `fallback.py` nên không tạo ngoại lệ kiến
trúc mới; `USER_LIST_SIZE`, mặc định 50) để UI dùng `st.selectbox` thay vì
nhập tay `user_id`. Lưu ý: `sequence_length` bị chặn ở 10 nên hầu hết user
bằng nhau — thứ tự chỉ mang tính xác định (tie-break theo `user_id`).

Panel **"Lịch sử tương tác"** (cột phải của UI): `GET /users/{user_id}/history`
(`api/history.py`) ghép lịch sử batch từ Feast (`user_recent_items`, tối đa
10 item + `event_time_sequence`) với Redis `recent_items` (tối đa 20 item,
phiên hiện tại, không có timestamp → nhãn `session`), mới nhất trước, làm
giàu bằng `FallbackCatalog.metadata()`. Chọn đúng 2 nguồn ranker đang dùng
(`sequence_source.py`) thay vì đọc `source-db` (ngoại lệ kiến trúc mới, cần
ADR) hoặc `silver.interactions` (không đổi ngay sau `/interact`) — nên panel
phản ánh đúng những gì model "nhìn thấy". Không cache, lỗi nguồn nào → rỗng
thay vì 5xx. Dữ liệu Amazon seed là `rating` chứ không phải `purchase`, nên
panel gọi là "lịch sử tương tác".

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế triển khai | Đánh giá |
|---|---|---|
| `jobs/candidates/popular_items.py` (job riêng, vật chất hoá Redis) | `fallback.py` tự truy vấn `gold.item_features` lúc startup + refresh định kỳ | Đủ rẻ ở quy mô ~1565 sản phẩm; không cần thêm 1 tầng Redis materialization |
| `jobs/candidates/similar_items.py` (Phase 4) + `jobs/materialize/export_similar_items_to_redis.py` (Phase 5) — 2 file | Gộp thành 1 file `jobs/candidates/similar_items.py`, chạy trong `training-job` | Tránh dựng thêm 1 Docker image mới chỉ cho 1 script nhỏ — `training-job` đã có sẵn torch (đọc embedding) + mlflow (load model) |
| `prometheus-fastapi-instrumentator` (mục 9.1 báo cáo kỹ thuật) | `prometheus_client` thuần + middleware tự viết | **Không phải lựa chọn tuỳ tiện** — `prometheus-fastapi-instrumentator` ghim `starlette<1.0.0`, xung đột trực tiếp với `feast==0.66.0` (`starlette>=1.0.1`) đang dùng trong cùng image. `prometheus_client` không phụ thuộc web framework nào, né xung đột hoàn toàn |
| `feast==0.66.0` (từ Phase 3) | Hạ xuống `feast==0.55.0` (cả `serving` lẫn `materialize-job`) | Xung đột **thật, không thể tránh khác** giữa `feast>=0.66.0` và `mlflow-skinny==3.12.0` (đã pin từ Phase 0/4) — xem [ADR 0007](../decisions/0007-downgrade-feast-for-mlflow-compat.md) |
| MLflow "trạng thái production" | Alias `production` (MLflow hiện đại, đã quyết định từ Phase 4) | Không đổi gì thêm ở Phase 5 — `model_loader.py` dùng đúng `registry_client.get_production_version()` đã có |
| Không nêu rõ vai trò `recent_items` khi model là sequence model | Hướng B (mục 2.1) — chốt cùng người dùng | Giải quyết đúng điểm ADR 0004 để ngỏ, không phải lệch phát sinh ngoài dự kiến |

## 4. Definition of Done — đối chiếu

Chạy thật trên toàn bộ hạ tầng (Postgres, Redis, MLflow, Iceberg/MinIO,
Feast) — không mock:

| Tiêu chí | Trạng thái | Bằng chứng |
|---|---|---|
| `/recommend/homepage`, `/recommend/similar` trả 200 ở cả 3 tình huống (cache hit, cache miss + model thật, model/feature không sẵn sàng → fallback) | ✅ | `curl` 2 lần liên tiếp (miss rồi hit, cùng payload); user `cold-start-user-xyz` (không có `gold.user_features`) → `source:"fallback"`, `200` |
| `/metrics` đúng định dạng Prometheus | ✅ | `curl /metrics` — có `# HELP`/`# TYPE`, series `http_requests_total`/`http_request_duration_seconds` theo đúng method/path/status_code |
| `POST /interact` rồi gọi lại `/recommend/homepage` — danh sách phản ánh rõ thay đổi | ✅ | User `AGS4GEV5TWDUAXB2M7FHJPHM2H7A` tương tác `B0BKVH96NL` → toàn bộ top-10 sau đó là 10 sản phẩm tương tự `B0BKVH96NL` (đúng thứ tự `similar_items:B0BKVH96NL` trong Redis) |
| Chạy `ui/app.py`, bấm "Đã mua" → gợi ý tự cập nhật, không thao tác thêm | ✅ (xác nhận cơ chế; UI reachable, gọi đúng 2 endpoint đã kiểm chứng ở trên) | `ui` container `healthy`, gọi `http://serving:8000/health` thành công qua mạng Docker nội bộ; nút bấm gọi đúng `POST /interact` + `st.rerun()` |

**Kết luận: Phase 5 đạt Definition of Done, sẵn sàng cho Phase 6
(Orchestration/Airflow).**

Số liệu phụ (tham khảo): `similar_items` job xử lý 1565/1565 item;
`serving/tests/` 20/20 pass (unit thuần + smoke test 3 nhánh qua
`TestClient`, không cần infra thật).

## 5. Việc còn để lại cho phase sau

- **Prometheus/Grafana hạ tầng thật** (scrape config, dashboard JSON) —
  Phase 7, `serving` đã có `/metrics` sẵn sàng để scrape.
- **`tests/load/locustfile.py`** — không thuộc Phase 5 (không nằm trong CI,
  dùng để tạo traffic demo trước khi trình bày — làm khi cần).
- **`tests/data_quality/test_train_serve_skew.py`** (so feature offline
  Gold vs online Feast) — ghi nhận trong báo cáo kỹ thuật mục 11 là hướng
  phát triển, chưa bắt buộc cho MVP.
- **Đăng ký alerting** khi metric vượt ngưỡng — chưa làm (Alertmanager,
  Phase 7 mở rộng).
- Model chất lượng còn thấp (kế thừa từ Phase 4, xem
  `phase-4-training.md` mục 4) — không chặn Phase 5 vì DoD chỉ yêu cầu quy
  trình đúng, không đặt ngưỡng chất lượng.
- `fastapi`/`uvicorn`/`redis` trong `requirements/serving.txt` cố ý chưa
  ghim cứng version (xem mục 3, ADR 0007) — nên chốt version cụ thể
  (`fastapi==0.141.1`, `uvicorn==0.34.0`) khi ổn định lâu dài, tránh
  version trôi giữa các lần build.

## 6. Danh sách file đã triển khai

### `libs/` (version 0.4.0)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `libs/src/reco_mlops_libs/ranking/gru4rec.py` | `GRU4RecNet` (kiến trúc, di chuyển từ `jobs/training/`) + `PAD_INDEX` | `torch` không phải dependency của `libs` — chỉ `training-job`/`serving` cài |

### `jobs/candidates/` (chạy trong image `training-job`)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `jobs/candidates/similar_items.py` | Cosine similarity trên GRU embedding → Redis `similar_items:{id}` | CLI `--top-k` (10), `--ttl-days` (7); đọc `item_vocab.json` từ đúng run đã promote |

### `serving/` (image `serving`)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `serving/app/main.py` | Entrypoint FastAPI, `lifespan` khởi động poll model + fallback | `uvicorn serving.app.main:app` |
| `serving/app/core/config.py` | Đọc env (`Settings`) | `MODEL_POLL_INTERVAL_SECONDS`, `FALLBACK_REFRESH_INTERVAL_SECONDS`, `CACHE_TTL_SECONDS`, `RECENT_ITEMS_*` |
| `serving/app/core/model_loader.py` | `ModelLoader` (poll registry), `ServingRanker(RankingModel)` | `REGISTERED_MODEL_NAME = "reco-mlops-gru4rec"` |
| `serving/app/core/sequence_source.py` | Merge chuỗi Feast + Redis (Hướng B) | `MAX_SEQUENCE_LENGTH = 10` |
| `serving/app/core/cache.py` | Redis cache GET/SET/invalidate | Key `cache:homepage:*`/`cache:similar:*` |
| `serving/app/core/fallback.py` | `FallbackCatalog`, `rank_items_by_popularity()` | Nguồn: `gold.item_features` |
| `serving/app/core/recency_boost.py` | `boost()` + `fetch_similar_items()` | Đọc `similar_items:{id}` |
| `serving/app/core/source_db_writer.py` | Ghi `interactions`, validate FK | `UnknownEntityError` → 404 |
| `serving/app/core/metrics.py` | `prometheus_client` thuần (không dùng `prometheus-fastapi-instrumentator`, xem mục 3) | `/metrics` |
| `serving/app/api/homepage.py` | `GET /recommend/homepage` | 3 nhánh: cache/model/fallback |
| `serving/app/api/similar.py` | `GET /recommend/similar` | Đọc thẳng `similar_items:{id}` |
| `serving/app/api/interact.py` | `POST /interact` | Ghi (A) Redis + (B) source-db, invalidate cache |
| `serving/app/schemas/models.py` | Pydantic request/response | Validator khớp CHECK constraint `interactions` |
| `serving/tests/test_ranking_merge.py` | Unit test `merge_sequence`/`to_indices`/`boost` | Hàm thuần, không cần infra |
| `serving/tests/test_fallback_selection.py` | Unit test `rank_items_by_popularity` | Hàm thuần |
| `serving/tests/test_endpoints_smoke.py` | Smoke test 3 nhánh DoD qua `TestClient` | Fake Redis/ranker/fallback, không cần infra thật |

### `ui/` (image `ui`)

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `ui/app.py` | Streamlit demo | `SERVING_BASE_URL` |

### Hạ tầng

| Đường dẫn | Vai trò | Config/setting đáng chú ý |
|---|---|---|
| `infra/docker/serving/Dockerfile` | Image serving | `torch` cài riêng qua `--index-url` CPU-only |
| `infra/docker/ui/Dockerfile` | Image ui | Nhẹ, không torch/feast/mlflow |
| `requirements/serving.txt` | Dependency serving | `feast[redis,aws]==0.55.0` (hạ version, ADR 0007), `fastapi`/`uvicorn`/`redis` không ghim cứng |
| `requirements/ui.txt` | Dependency ui | `streamlit`, `requests` |
| `requirements/materialize.txt` | Đã sửa | `feast[redis,aws]` hạ xuống `0.55.0` (đồng bộ với serving, ADR 0007) |
| `requirements/training.txt` | Đã sửa | Thêm `redis` cài riêng trong Dockerfile (không qua file này, tránh lẫn index CPU-only) |
| `docker-compose.yml` | Service `serving`, `ui` (không `profiles`, luôn chạy cùng `make up`); `training-job` thêm `REDIS_HOST`/`REDIS_PORT` | `SERVING_PORT`, `UI_PORT` |
| `docs/decisions/0007-downgrade-feast-for-mlflow-compat.md` | ADR hạ version feast | — |
