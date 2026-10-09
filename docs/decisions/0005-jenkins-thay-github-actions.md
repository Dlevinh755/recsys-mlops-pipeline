# 0005 — Jenkins thay GitHub Actions cho CI/CD

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-09-10
- **Phase liên quan**: Phase 8

## Bối cảnh

`bao-cao-ky-thuat.md` mục 7.3 và `de-xuat-trien-khai.md` Phase 8 chỉ định cụ
thể CI/CD dùng GitHub Actions, với 3 workflow file:

- `.github/workflows/ci.yml` — lint, unit test, integration test, và job
  `model-smoke-test` (train + evaluate trên `tests/model/sample_data/` cố
  định seed)
- `.github/workflows/build-push-images.yml` — build/push Docker image, tag
  theo `git rev-parse --short HEAD`
- `.github/workflows/model-cd.yml` — thông báo/ping khi có model mới được
  promote (không bắt buộc vì `model_loader.py` đã tự poll registry)

Phase 8 chưa bắt đầu code (xem `docs/STATUS.md`) nên đây là quyết định thiết
kế trước khi implement, không phải sửa lại code đã có.

## Quyết định

Dùng **Jenkins** (declarative pipeline, `Jenkinsfile`) thay cho GitHub
Actions cho toàn bộ CI/CD, giữ nguyên cách tách 2 luồng độc lập
code/hạ-tầng-CI và model-smoke-test-CI đã mô tả ở mục 7.3:

- `Jenkinsfile` (multibranch pipeline, root repo) — thay cho `ci.yml`:
  stage `Lint`, `Unit test`, `Integration test`, stage `Model smoke test`
  (train + evaluate trên `tests/model/sample_data/`, tách stage riêng để
  log/kết quả không lẫn với test code thường)
- `Jenkinsfile.build-push` hoặc thêm stage `Build & Push Images` vào cùng
  `Jenkinsfile` (dùng `parallel` cho **7 image job-group có business logic
  tự viết**: `extract`, `transform`, `materialize`, `training`, `serving`,
  `airflow`, `monitoring`, `ui` — xem bảng phân loại đầy đủ trong
  `PHONG-VAN-QA-BAO-VE.md` mục 2; `mlflow` và chính `jenkins` build local,
  không qua stage này vì chỉ là wrapper mỏng quanh image upstream/chính
  công cụ chạy CI) — thay cho `build-push-images.yml`, vẫn tag image theo
  `git rev-parse --short HEAD` (nguyên tắc #8 CLAUDE.md, không đổi)
- Bỏ `model-cd.yml` — vẫn giữ nguyên lý do gốc (serving tự poll registry),
  không liên quan tới việc đổi CI engine

Jenkins chạy trên chính Docker Compose stack cục bộ. **Agent: một agent duy
nhất (`agent any`)** ở cấp toàn bộ pipeline — không dùng agent Docker riêng
cho từng stage. Container Jenkins (base `jenkins/jenkins:lts`, thêm
`docker-ce-cli`) mount `/var/run/docker.sock` (pattern sibling-container,
**không** dùng Docker-in-Docker/`dind`, để tránh yêu cầu chạy container
`--privileged`) — mọi lệnh `docker build`/`docker compose` gọi từ shell step
chạy thẳng ra Docker daemon của host. Không dùng dịch vụ CI cloud — phù hợp
với nguyên tắc "chạy hoàn toàn local" của toàn project.

**Registry đích: Docker Hub**, namespace
`docker.io/<dockerhub-username>/mlops4rec-<job-group>:<git-sha>`, một
repository riêng cho mỗi job group. Đăng nhập qua Jenkins Credentials Store
(`usernamePassword`, id `dockerhub-creds`, dùng Access Token thay vì
password tài khoản), `docker login --password-stdin` (không truyền token
qua đối số dòng lệnh, tránh lộ trong `docker history`/`ps aux`). Image được
public trên Docker Hub (không chứa secret/model weight — model đi qua
MLflow registry, không nằm trong image), chấp nhận đánh đổi bước push cần
internet ra ngoài (phần lõi vận hành/business logic vẫn chạy local, chỉ nơi
lưu artifact build mở ra ngoài).

**Trigger: SCM polling** (`pollSCM('H/5 * * * *')`), không dùng webhook —
dù code host trên GitHub, Jenkins vẫn không có public endpoint để GitHub
gọi vào, nên Jenkins tự định kỳ kéo về thay vì chờ được gọi. Đánh đổi: có
độ trễ vài phút, chấp nhận được ở quy mô một người/tần suất commit của một
khóa luận. Nâng cấp sau này nếu cần tức thời hơn: expose Jenkins qua tunnel
rồi đổi `pollSCM` sang `githubPush()`, không cần viết lại pipeline.

## Lý do

Người thực hiện chủ động muốn vận hành Jenkins (kỹ năng DevOps muốn thể
hiện/luyện tập cho khóa luận) thay vì phụ thuộc GitHub Actions — đánh đổi:

- Được: tự quản lý toàn bộ pipeline cục bộ, không phụ thuộc GitHub là nơi
  host code (nhất quán hơn với tinh thần "chạy hoàn toàn local" của
  project), thể hiện kỹ năng cấu hình CI server thật thay vì YAML có sẵn.
- Mất: phải tự vận hành thêm một service (Jenkins) trong hạ tầng local,
  không có UI PR-check tích hợp sẵn như GitHub Actions, cấu hình
  multibranch/webhook phức tạp hơn nếu code không host trên GitHub.

## Hệ quả

- `de-xuat-trien-khai.md` Phase 8 checklist: mọi mục nhắc `.github/workflows/
  *.yml` cần đọc là "tương đương trong `Jenkinsfile`" — không sửa lại nội
  dung `de-xuat-trien-khai.md` gốc (theo CLAUDE.md, đây vẫn là nguồn kế
  hoạch, `docs/modules/phase-8-*.md` sẽ là nguồn as-built khi Phase 8 code
  xong).
- `PROJECT-OVERVIEW-CV.md` (mục DevOps/Platform, tech stack) đang liệt kê
  "GitHub Actions (planned CI/CD)" — cần cập nhật thành Jenkins khi bắt đầu
  code Phase 8 (không sửa ngay bây giờ vì nguyên tắc "không viết tài liệu
  mô tả tính năng trước khi có code" áp dụng cho tài liệu tiến độ, còn file
  CV là tài liệu thiết kế riêng — sẽ đồng bộ cùng lúc Phase 8 implement).
- Thêm một service Jenkins vào hạ tầng local khi Phase 8 bắt đầu — theo
  CLAUDE.md mục "Không làm", service phục vụ CI không tính vào
  `docker-compose.yml` chính nếu không cần cho runtime của hệ thống; đặt
  trong `docker-compose.override.yml` hoặc một compose file riêng
  (`infra/jenkins/docker-compose.jenkins.yml`, theo đúng pattern
  `infra/mlflow/`, `infra/source-db/` đã có).
- Không ảnh hưởng nguyên tắc kiến trúc #1-8 khác trong CLAUDE.md — CI engine
  không phải business logic, các job vẫn chạy qua `python -m jobs.xxx.run`
  y hệt cách gọi local.
- `docker-compose.yml`: mỗi service job cần thêm cả `image:` (tham chiếu
  `${DOCKERHUB_USERNAME:-local}/mlops4rec-<job>:${IMAGE_TAG:-dev}`) lẫn
  `build:` đang có sẵn — `docker compose build` (dev) build local tag mặc
  định, `docker compose pull` (không build) kéo đúng image đã qua CI theo
  `IMAGE_TAG=<git-sha>`. Không cần hai file compose riêng cho hai chế độ.

## Tham chiếu

Sẽ bổ sung đường dẫn file code cụ thể (`Jenkinsfile`, `infra/jenkins/`) khi
Phase 8 thực sự triển khai — cập nhật `docs/modules/phase-8-*.md` lúc đó.

## Cập nhật 2026-09-14

Bổ sung các chi tiết thiết kế còn thiếu ở bản gốc (trước Phase 8, vẫn ở giai
đoạn thiết kế nên sửa trực tiếp thay vì viết ADR mới — không phải sửa quyết
định đã áp dụng cho code đã có):

- Danh sách image qua `Build & Push` sửa từ 5 (thiếu `materialize`,
  `airflow`, `ui`) thành 7 — `materialize` đã có code thật từ Phase 3
  (`infra/docker/materialize/Dockerfile`) nhưng bị bỏ sót khỏi bản gốc.
- Chốt cụ thể: agent (`agent any`, sibling-container qua `docker.sock`,
  không `dind`), registry đích (Docker Hub), cơ chế trigger (SCM polling).
  Bản gốc chỉ nói "Docker-in-Docker hoặc mount `docker.sock`" (chưa chọn) và
  không nói registry/trigger là gì.
- Thảo luận đầy đủ (câu hỏi phỏng vấn mẫu, lý do từng lựa chọn, đánh đổi) ở
  `PHONG-VAN-QA-BAO-VE.md` mục 4 — file này chỉ giữ bản tóm tắt quyết định.

## Cập nhật 2026-09-15

Chốt thứ tự stage trong `Jenkinsfile` và phạm vi service của từng test —
vẫn ở giai đoạn thiết kế (Phase 8 chưa bắt đầu), sửa trực tiếp bản gốc:

- **Thứ tự stage**: `Build test images` (build local `transform-job` +
  `training-job`, chưa push) → `Lint` → `Unit Test` → `Integration Test` →
  `Model Smoke Test` → `Build & Push Images`. Ở stage cuối, `transform`/
  `materialize`/`training` **không build lại** — chỉ `docker tag`/`docker
  push` đúng image đã build+test ở stage đầu, tránh anti-pattern "test một
  bản, ship một bản khác"; `serving`/`monitoring`/`airflow`/`ui` build lần
  đầu tại đây vì chưa có test stage nào exercise chúng ở MVP hiện tại.
- **Model Smoke Test dùng MLflow file-based**
  (`MLFLOW_TRACKING_URI=file:///tmp/mlruns`), không `docker compose up`
  service `mlflow`/`mlflow-db`/`minio` — `sample_data` là fixture cố định,
  không đọc Iceberg, nên không cần hạ tầng thật; đường ghi S3 artifact qua
  MinIO đã được test riêng ở `tests/integration/test_iceberg_writer.py`,
  không cần lặp lại cho MLflow.
- Bảng đầy đủ "stage nào cần service thật, test cụ thể file/hàm nào" ở
  `PHONG-VAN-QA-BAO-VE.md` mục 4 (câu "Cụ thể từng stage/service test cái
  gì?") — file này chỉ giữ bản tóm tắt quyết định, không lặp lại bảng.
