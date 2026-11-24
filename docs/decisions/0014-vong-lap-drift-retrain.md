# 0014 — Vòng lặp drift → retrain khép kín

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-24
- **Phase liên quan**: Phase 6 (Airflow), Phase 7 (monitoring), Phase 10 (AGF)

## Bối cảnh

Trước đó Evidently chỉ sinh báo cáo và mọi DAG `schedule=None`: phát hiện drift
không kích hoạt gì (đo 2026-11-24 sau khi nạp `incoming/`: `dataset_drift=true`,
`is_cold_start_item` JS 0,163, `rating` 0,0126). Nếu chỉ thêm "drift → retrain" thì
có bẫy: baseline là `train.parquet` cố định, nên retrain xong drift **vẫn báo true
mãi** và retrain lặp vô hạn. `phase-6-orchestration.md` ghi rõ "không chaining
tự động giữa các DAG"; quyết định này đổi điều đó cho đúng một DAG.

## Quyết định

1. **Job quyết định, DAG chỉ nối** (nguyên tắc #1).
   `jobs/monitoring/retrain_decision.py`: hàm thuần `decide(signals, policy)`.
   Thoát **0 = retrain, 99 = bỏ qua**; quyết định và lý do ghi lên
   `monitoring-reports/retrain_decision/<ts>.json`. Không truyền dữ liệu qua XCom.
2. **Chính sách** (env, mặc định trong `.env.example`, truyền qua `docker-compose.yml`):
   `RETRAIN_MIN_NEW_ROWS=200`, `RETRAIN_NEW_ROWS_RATIO=0.20`,
   `RETRAIN_MAX_MODEL_AGE_DAYS=14`, `RETRAIN_COOLDOWN_HOURS=24`. Luật: trong cooldown
   kể từ lần train gần nhất → bỏ qua; ngược lại retrain nếu
   (`dataset_drift` **và** dòng mới ≥ 200) **hoặc** dòng mới/tham chiếu ≥ 20%
   **hoặc** model production ≥ 14 ngày **hoặc** chưa có production. Chỉ có hai cột
   được so sánh nên 1 cột drift đã đủ `dataset_drift` (share 0,5): vì vậy cần ngưỡng
   số dòng đi kèm; không đổi preset Evidently.
3. **"Dòng mới" đếm so với version train gần nhất, không so với baseline drift**
   (lệch nhẹ so với spec, mục B2). `train_agf` log param `silver_rows` và
   `iceberg_snapshot_id.silver.interactions` cho mỗi version;
   `new_rows = silver_hiện_tại − silver_rows của version mới nhất` (promote hay không).
   Lý do: challenger bị từ chối đã dùng hết số dòng đó; nếu so với baseline (theo
   model production) thì cứ hết cooldown lại retrain trên đúng dữ liệu cũ. Version
   chưa có `silver_rows` (v1–v7) rơi về số dòng baseline drift, ghi
   `reference_source: "drift_baseline"`.
4. **Baseline dịch theo model production.** `jobs/monitoring/refresh_baseline.py`:
   khi alias `production` khác `model_version` trong
   `monitoring-baseline/current.meta.json`, ghi `current.parquet` (`parent_asin`,
   `rating`) từ **snapshot Silver lúc version đó train** (time travel) cùng meta
   `{model_version, rows, silver_snapshot_id, source, created_at}`. Version không đổi
   → không làm gì; không promote → baseline không đổi. `data_drift_report` đọc
   `current.parquet` nếu có, không thì `train.parquet`.
   - Version **không có** param snapshot (trước ADR này) → baseline **không dịch**.
     Ghi Silver hiện tại sẽ nhận vơ những dòng model chưa thấy và che drift thật
     (lần chạy đầu của thử nghiệm đã làm đúng như vậy cho v4 và bị sửa lại).
   - Snapshot đã bị `expire_snapshots` (ADR 0012, mặc định 7 ngày) → rơi về Silver
     hiện tại, ghi `source: "silver_current"`.
5. **DAG** `airflow/dags/dag_continuous_training.py`: `data_drift_report →
   retrain_decision → run_dag_ingest → run_dag_transform →
   run_dag_features_candidates → run_dag_materialize → run_dag_training →
   refresh_baseline`, các `run_*` là `TriggerDagRunOperator(wait_for_completion=True)`.
   `retrain_decision` dùng `skip_on_exit_code=99` (đã có sẵn trong `DockerOperator`
   của provider 3.14.1, không phải sửa `JobDockerOperator`); các task sau bị
   *skipped* và run vẫn xanh. `@daily`, tạo ở trạng thái tạm dừng,
   `max_active_runs=1`.
6. **`_defaults.py`: `is_paused_upon_creation=False`** cho các DAG cũ. Run của DAG
   đang tạm dừng không bao giờ bắt đầu, nên DAG con bị trigger phải không ở trạng
   thái đó. Các DAG cũ vẫn `schedule=None`, chạy tay như trước.
7. `data_drift_report` lưu thêm khoá `reco_summary` vào JSON (additive, phần còn lại
   vẫn là `report.as_dict()` của Evidently).

## Lý do

- Đọc kết quả qua XCom: trái nguyên tắc #1. Gộp task của các DAG cũ vào DAG mới: giữ
  cùng một chuỗi ở hai nơi; trigger giữ nguyên các DAG chạy tay.
- Baseline = `train.parquet` mãi: drift luôn true sau lần retrain đầu tiên.
- Cooldown một mình chặn lặp vô hạn nhưng vẫn lặp mỗi ngày trên dữ liệu cũ khi
  challenger bị từ chối — hence mục 3.

## Kết quả chạy thật (2026-11-24, `dag_continuous_training`, `RETRAIN_COOLDOWN_HOURS=0`)

Xuất phát: production v4, baseline `train.parquet` (14.748 dòng), Silver 18.456 dòng
(đã nạp `incoming/`), v5–v7 đã đăng ký và không được promote.

| Bước | Kết quả |
|---|---|
| `data_drift_report` | `dataset_drift=true`, `is_cold_start_item` drift (0,163), `rating` không (0,0126) |
| `retrain_decision` | `retrain=true`: "dataset drift (is_cold_start_item) with 3708 new rows >= 200" và "3708/14748 = 25.1% >= 20%" |
| Chuỗi ingest → … → training | Xanh, ~5 phút. `train_agf` (v8) chấm champion v4 trên cùng split: 0,0680 |
| `promote` | `comparison: same_split`, 0,0847 > 0,0680 → **promote v8**. Serving `/health` báo `model_version: 8` |
| `refresh_baseline` | `current.parquet` 18.456 dòng, `source: silver_snapshot`, `silver_snapshot_id` khớp snapshot lúc train |
| Chạy lại ngay | `data_drift_report`: baseline = v8, không drift (điểm 0,0). `retrain_decision`: `retrain=false`, `new_rows = 0`. 6 task sau **skipped**, run **xanh**. Không có container job nào bị bỏ sót |

Lần chạy đầu tiên (trước khi sửa) cho thấy hai vấn đề, đã xử lý: image
`training:dev` còn cũ nên v7 không có số same-split và không được promote (lỗi quy
trình build local, không phải thiết kế), và `refresh_baseline` ghi Silver hiện tại
cho v4 — xem mục 4.

## Hệ quả

- **Ngoại lệ so với Phase 6**: có cross-DAG trigger, chỉ qua `dag_continuous_training`.
- **Chưa kiểm chứng**: lịch `@daily` thật (chỉ chạy bằng unpause và trigger tay);
  kịch bản challenger bị từ chối (cooldown và `silver_rows` chống lặp mới được kiểm
  chứng bằng unit test, chưa bằng một lần bị từ chối thật); DAG chưa chạy trong
  Jenkins (chỉ test mới được thêm vào Jenkinsfile).
- **Hạn chế đã biết**: `current` của Evidently là toàn bộ Silver nên có chứa cả các
  dòng của baseline; khi dòng mới chỉ là phần nhỏ, drift bị pha loãng và có thể
  không vượt ngưỡng (khi đó luật tỷ lệ dòng và tuổi model là lưới an toàn).
- Production đã chuyển v4 → v8 trên stack local khi chạy thử.
- Demo: đặt `RETRAIN_COOLDOWN_HOURS=0` trong `.env` rồi
  `docker compose restart airflow-scheduler` (cấu hình compose được cache theo tiến
  trình).

## Tham chiếu

`jobs/monitoring/{baseline,retrain_decision,refresh_baseline,data_drift_report}.py`,
`jobs/training/train_agf.py`, `airflow/dags/dag_continuous_training.py`,
`airflow/dags/_defaults.py`, `tests/monitoring/`,
`side_doc/SPEC-retrain-loop-and-promote-gate.md`, ADR 0010, 0012, 0013.
