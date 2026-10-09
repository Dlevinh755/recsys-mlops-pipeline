# Phase 8 — CI/CD, test, tài liệu, đóng gói `libs/`

> Trạng thái: **Hoàn thành** (trừ 1 việc phụ thuộc ngoài code — xem mục 5).
> Đối chiếu với `de-xuat-trien-khai.md` mục "Phase 8" và
> [ADR 0005](../decisions/0005-jenkins-thay-github-actions.md) (Jenkins
> thay GitHub Actions — mọi chỗ checklist gốc nhắc `.github/workflows/*.yml`
> đọc là "tương đương trong `Jenkinsfile`"). Phụ thuộc: toàn bộ Phase 1–7.

Thư mục code: `Jenkinsfile`,
`infra/jenkins/`, `infra/docker/jenkins/`,
`tests/model/`, `tests/data_quality/`,
`serving/tests/test_model_reload.py`, `docs/`.

---

## 1. Mục tiêu ban đầu

Siết chất lượng và khả năng tái lập: CI chạy lint/test/model-smoke-test tự
động trên mỗi commit, đóng gói `libs/` đúng cách, tài liệu phản ánh đúng
thực tế đã code.

## 2. Đã triển khai

### 2.1 `Jenkinsfile` (repo root)

6 stage, chạy trên 1 agent duy nhất (container Jenkins tự mount
`docker.sock`, sibling-container — xem
[ADR 0005](../decisions/0005-jenkins-thay-github-actions.md) và
`PHONG-VAN-QA-BAO-VE.md` mục 4 cho lý do đầy đủ từng quyết định):

1. **Prepare** — tạo `.env` từ `.env.example` nếu chưa có.
2. **Build test images** — `transform-job`/`training-job`/`materialize-job`
   (3 image cần để chạy test), local, chưa push.
3. **Lint** — `ruff check` qua bind-mount checkout thật (`-v $(pwd):/repo:ro`)
   trong `transform-job`, vì `jobs/`/`libs/`/`serving/`/`airflow/`/`ui/`
   không nằm cùng 1 image job nào.
4. **Unit Test** — chạy tách theo 3 image tương ứng module mỗi test file
   import (`jobs.extract` chỉ có ở `extract-job`, `jobs.training` chỉ có ở
   `training-job`) — phát hiện thật khi implement: bản thiết kế gốc giả
   định chạy hết trong `transform-job` không đúng nữa từ khi có
   `tests/unit/training/` (Phase 4).
5. **Integration Test** — dựng full stack cô lập
   (`COMPOSE_PROJECT_NAME=reco-ci-${BUILD_NUMBER}`, `docker-compose.ci.yml`
   bỏ hết port host — xem mục 2.3), chạy `tests/integration` (PyIceberg
   thật) rồi cả pipeline thật (extract→...→feast_materialize) để có dữ liệu
   cho `tests/data_quality` (cần cả Iceberg lẫn Feast/Redis, chạy trong
   `materialize-job`). Luôn `down --volumes` ở `post.always`.
6. **Model Smoke Test** — `MLFLOW_TRACKING_URI=file:///tmp/mlruns`, không
   `docker compose up` gì (ADR 0005 update 2026-09-15).
7. **Build & Push Images** — 8 image song song (`failFast` mặc định
   `false`, cố ý), `transform`/`materialize`/`training` **tag lại** image đã
   build+test ở stage 2 (không build lại — tránh "test 1 bản, ship 1 bản
   khác"); `extract`/`serving`/`airflow`/`monitoring`/`ui` build lần đầu tại
   đây. Bọc `catchError(buildResult: 'SUCCESS', stageResult: 'FAILURE')` —
   thiếu `dockerhub-creds` thật không làm hỏng cả pipeline, chỉ stage này
   fail rõ ràng (xem mục 5).

Đã validate cú pháp thật qua chính API linter của Jenkins
(`/pipeline-model-converter/validate`), không chỉ đọc bằng mắt.

### 2.2 Hạ tầng Jenkins

- `infra/docker/jenkins/Dockerfile` — `jenkins/jenkins:lts-jdk17` + Docker
  CLI/Compose plugin (apt repo chính thức Docker) + plugin qua
  `plugins.txt` (`git`, `workflow-aggregator`, `credentials-binding`,
  `configuration-as-code`, `timestamper`).
- `infra/jenkins/casc.yaml` — Configuration as Code: 1 user admin (từ env),
  1 credential `dockerhub-creds` (username/password từ env, rỗng cho tới
  khi set `DOCKERHUB_TOKEN` thật). **Không** tạo job Multibranch Pipeline
  qua JCasC — cần URL GitHub thật (chưa có), tạo tay 1 lần qua UI khi có
  repo (xem mục 5).
- `infra/jenkins/docker-compose.jenkins.yml` — standalone, **không** thuộc
  `docker-compose.yml` chính (CLAUDE.md: service chỉ phục vụ CI không vào
  compose chính) — bật riêng bằng
  `docker compose -f infra/jenkins/docker-compose.jenkins.yml up -d --build`.
- `docker-compose.ci.yml` — override chỉ dùng trong stage Integration Test,
  `ports: !reset []` cho mọi service publish port host (Compose *merge*
  chứ không *replace* list theo mặc định — `ports: []` không đủ, phải dùng
  tag `!reset`, phát hiện thật khi kiểm chứng, không phải suy đoán).

### 2.3 Test mới

| File | Chạy trong image | Cần hạ tầng thật? |
|---|---|---|
| `tests/model/sample_data/` + `test_training_smoke.py` | `training-job` | Không — `FakeTable` chỉ giả lập đúng 1 method `.scan().to_arrow()` mà code thật gọi, `MLFLOW_TRACKING_URI=file://` |
| `tests/data_quality/test_train_serve_skew.py` | `materialize-job` | Có — Lakekeeper + Feast/Redis thật |
| `serving/tests/test_model_reload.py` | `training-job` (không phải `serving/`'s test suite — cần `torch` để tạo version thật) | Có — `serving` + MLflow thật đang chạy |

`jobs/training/train_sequence.py` được refactor tách `run_training()` (logic
thuần) khỏi `main()` (CLI wiring Iceberg/MLflow-URI) để
`test_training_smoke.py` gọi được mà không cần Iceberg — hành vi `main()`
không đổi.

`serving/app/main.py::/health` thêm field `model_version` (đọc
`ModelLoader.get_current()`) — cần để `test_model_reload.py` quan sát được
serving đã nhận version mới hay chưa, không gate healthcheck theo nó.

### 2.4 `libs/` đóng gói

Đã đúng chuẩn từ Phase 4 (`pyproject.toml`, `CHANGELOG.md`, mọi
`requirements/<job>.txt`/Dockerfile cài qua `-e ../libs` hoặc `-e /app/libs`)
— không có việc mới phải làm, chỉ xác nhận lại ở đây theo đúng checklist.
Riêng `requirements/dev.txt` (dùng chung cho CI: `pytest`/`ruff`/...) có
dòng `-e ../libs` cho dev chạy trên host — khi cài **trong** container job
(đã có `-e /app/libs` riêng), dòng đó bị lọc bỏ trước khi cài phần còn lại
(`grep -v '^-e '`), tránh trùng đường dẫn tương đối sai ngữ cảnh — lỗi thật
gặp phải khi build, không phải phòng ngừa lý thuyết.

### 2.5 `docker-compose.yml` — `image:` + `build:`

8 service job-group (`extract-job`, `transform-job`, `materialize-job`,
`training-job`, `monitoring-job`, `serving`, `ui`, `airflow-*`) đều thêm
`image: ${DOCKERHUB_USERNAME:-local}/mlops4rec-<job>:${IMAGE_TAG:-dev}` bên
cạnh `build:` có sẵn — `docker compose build` (dev) luôn build local;
`docker compose pull` kéo đúng image Jenkins đã build+test+push theo
`IMAGE_TAG=<git sha>`.

### 2.6 Tài liệu

`docs/architecture.md`, `docs/runbook.md` (bao gồm rollback model thủ
công), `docs/data_dictionary.md`, `docs/model_card.md` (mới) — cùng file
`docs/modules/phase-8-cicd.md` này.

## 3. Khác biệt so với đề xuất ban đầu

| Đề xuất gốc | Thực tế | Lý do |
|---|---|---|
| `.github/workflows/*.yml` | `Jenkinsfile` + `infra/jenkins/` | Đã quyết trước khi code — [ADR 0005](../decisions/0005-jenkins-thay-github-actions.md) |
| Unit Test chạy 1 image | Chạy tách 3 image (`extract-job`/`training-job`/`transform-job`) | `tests/unit/` đã có test cho nhiều job group từ Phase 4, không job nào cõng hết dependency |
| `ports: []` để tắt publish port trong CI | Phải dùng `ports: !reset []` | Compose merge (không replace) list theo mặc định — phát hiện thật lúc kiểm chứng |

Không cần ADR mới cho 2 điểm còn lại — đều là sửa lỗi kỹ thuật khi
implement, không đổi quyết định kiến trúc nào.

## 4. Definition of Done — đối chiếu

| Yêu cầu | Kết quả |
|---|---|
| CI chạy tự động trên mỗi commit, gồm model-smoke-test | Jenkinsfile có `pollSCM`, đã validate cú pháp qua chính Jenkins linter; **chưa trigger được qua commit thật** vì chưa có remote Git (xem mục 5) — mọi stage đã chạy thật thủ công với cùng lệnh Jenkinsfile dùng, tất cả pass (16 unit test, 5 integration test, 1 model-smoke-test, 1 skew test, 1 model-reload test, lint sạch) |
| Tài liệu phản ánh đúng code đã có | ✅ — toàn bộ 4 file mới + module doc này viết sau khi code xong |

## 5. Việc còn để lại

- **Chưa có Git remote thật** (project chưa `git init` tính tới trước Phase
  8 — nay đã có, xem lịch sử commit) và chưa có repo GitHub/Docker Hub
  token thật của người dùng → `pollSCM` chưa có gì để poll, stage
  `Build & Push Images` sẽ fail rõ ràng (không chặn pipeline nhờ
  `catchError`) cho tới khi set `DOCKERHUB_TOKEN` thật trong `.env`. Việc
  còn lại thuần thao tác tay, không phải code: (1) tạo repo GitHub, push
  code; (2) tạo Docker Hub Access Token, set `DOCKERHUB_USERNAME`/
  `DOCKERHUB_TOKEN`; (3) `docker compose -f infra/jenkins/docker-compose.jenkins.yml up -d --build`,
  tạo 1 job Multibranch Pipeline trỏ vào repo đó qua Jenkins UI.
- Tag `git rev-parse --short HEAD` cho image giờ đã khả thi (có git) nhưng
  vẫn cần CI thật chạy ít nhất 1 lần để xác nhận `GIT_SHA` trong
  `Jenkinsfile` (`sh 'git rev-parse --short HEAD'`) hoạt động đúng trong
  ngữ cảnh Jenkins Git plugin checkout (chưa kiểm chứng — cần remote thật).
- `expire_snapshots()` cho `compact_iceberg.py` (Phase 7) vẫn để lại.

## 6. Danh sách file đã triển khai

| Đường dẫn | Vai trò |
|---|---|
| `Jenkinsfile` | 6 stage, đã validate qua Jenkins linter |
| `infra/docker/jenkins/Dockerfile`, `plugins.txt` | Image Jenkins |
| `infra/jenkins/casc.yaml` | Admin user + credential `dockerhub-creds` |
| `infra/jenkins/docker-compose.jenkins.yml` | Standalone, không thuộc compose chính |
| `docker-compose.ci.yml` | `!reset` port cho Integration Test |
| `tests/model/sample_data/{item_catalog,user_sequences}.json`, `generate.py` | Fixture fixed-seed |
| `tests/model/test_training_smoke.py` | Model smoke test |
| `tests/data_quality/test_train_serve_skew.py` | Train/serve skew, hạ tầng thật |
| `serving/tests/test_model_reload.py` | Model reload, hạ tầng thật |
| `jobs/training/train_sequence.py` | Refactor `run_training()` tách khỏi `main()` |
| `serving/app/main.py` | `/health` thêm `model_version` |
| `serving/app/core/cache.py` | (Phase 7, không phải Phase 8 — không liệt kê lại) |
| `docker-compose.yml` | +`image:` cho 8 service job-group |
| `.env.example` | +`DOCKERHUB_USERNAME`, `IMAGE_TAG`, `JENKINS_*` |
| `docs/architecture.md`, `docs/runbook.md`, `docs/data_dictionary.md`, `docs/model_card.md` | Tài liệu mới |
