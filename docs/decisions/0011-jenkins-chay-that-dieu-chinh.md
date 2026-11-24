# 0011 — Điều chỉnh Jenkins khi chạy pipeline thật lần đầu

- **Trạng thái**: Đã áp dụng
- **Ngày**: 2026-11-24
- **Phase liên quan**: Phase 8 (CI/CD)

## Bối cảnh

ADR 0005 chốt Jenkins với **một `agent any`** cho cả pipeline, sibling-container
qua `docker.sock`, chạy bằng user mặc định của image `jenkins`. Cho tới
2026-11-24 các stage mới chỉ chạy tay bằng đúng lệnh của `Jenkinsfile`. Lần
đầu chạy qua Jenkins thật (build #1–#7) lộ ra bốn chỗ thiết kế gốc không chạy
được. Build #7 (commit `87b0837`) xanh toàn bộ 6 stage sau khi sửa cả bốn.

## Quyết định

1. **Container Jenkins chạy bằng `root`** (`user: root` trong
   `infra/jenkins/docker-compose.jenkins.yml`). Trên Docker Desktop,
   `/var/run/docker.sock` không thuộc nhóm của user `jenkins` nên mọi lệnh
   `docker` báo `permission denied while trying to connect to the docker API`.
2. **`COMPOSE_FILE` đặt ở cấp `environment` của pipeline**, gồm cả
   `docker-compose.ci.yml`. Stage Lint/Unit Test cũng gọi `docker compose run`
   nên kéo theo `catalog-db`...; thiếu file CI thì publish cổng 5434 và va với
   stack dev.
3. **Workspace cố định** `/srv/jenkins-ws/recsys-mlops` (`agent { node {
   customWorkspace ... } }` thay cho `agent any`), kèm mount cùng đường dẫn
   `/srv/jenkins-ws:/srv/jenkins-ws` vào container Jenkins. Bind mount tương
   đối trong `docker-compose.yml` do **daemon của máy host** phân giải; đường
   dẫn mặc định dưới `/var/jenkins_home` không tồn tại ở host nên Docker tạo
   thư mục rỗng (`minio-init` thoát mã 126).
4. **Thêm bước `extract-job` vào stage Integration Test**, trước
   `build_bronze`, với `DLT_PIPELINES_DIR=/tmp/dlt-pipelines`. Tài liệu Phase 8
   ghi pipeline chạy `extract→...` nhưng `Jenkinsfile` chưa từng có bước này, nên
   Bronze/Gold rỗng và `test_train_serve_skew` fail (build #6). State dlt đặt ở
   `/tmp` của container vì workspace cố định sẽ giữ state cũ giữa các build
   (cùng cơ chế gây ra việc mất 8 dòng fixture, `phase-10-agf.md` mục 7.3).

5. **Image của build mang tag `<GIT_SHA>`, và `Build & Push` chỉ tag lại**.
   Pipeline ép `DOCKERHUB_USERNAME=local`, `IMAGE_TAG=<GIT_SHA>` (không thừa
   hưởng từ môi trường container Jenkins); stage `Build test images` build thêm
   `extract` và `monitoring`; 8 image đẩy lên Docker Hub đều là image đã build và
   test trong cùng build. Lý do: biến `DOCKERHUB_USERNAME` của container Jenkins
   (có khi khởi động bằng `--env-file`) làm Compose build ra
   `dlvinh755/...:dev`, trong khi `Jenkinsfile` tag lại `local/...:dev` — build
   #7 và #8 đã push bản cũ còn sót từ build #6 cho `transform`, `materialize`,
   `training`. Với SHA trong tên, image cũ không thể tồn tại cho commit mới và
   `docker tag` fail rõ ràng nếu thiếu.
6. **Image Jenkins có `docker-buildx-plugin`** (BuildKit thay trình build cũ;
   chung cache với daemon host, build 3 service Airflow cùng Dockerfile gọn hơn).
7. **Model Smoke Test chạy với `--no-deps`, và có `docker compose down` ở
   `post.always` cấp pipeline.** Không có `--no-deps`, `run` bật lại các
   service phụ thuộc của `training-job` sau khi stage Integration Test đã `down`
   và không ai tắt: build #7 và #8 mỗi lần bỏ sót ~9 container (mlflow 1,6 GB).
   Chúng làm build #9 chạy 15 phút thay vì 1 phút ở bước integration test, rồi
   `Temporary failure in name resolution: lakekeeper`. Dọn hai stack đó xong, bộ
   integration test (23 test) chạy 2,5 phút trên stack cô lập.

## Lý do

Các phương án đã loại:

- **`group_add` đúng GID của socket** thay cho `root`: chặt hơn nhưng GID khác
  nhau theo máy/Docker Desktop, không ổn định cho đồ án chạy trên máy cá nhân.
- **Agent Docker riêng cho từng stage**: ADR 0005 đã loại vì cùng vấn đề bind
  mount, không đổi.
- **Mount `${WORKSPACE}` mặc định**: không có đường dẫn trùng giữa Jenkins và
  daemon host, chính là lỗi của mục 3.

## Hệ quả

- **Ngoại lệ so với ADR 0005 / `bao-cao-ky-thuat.md`**: Jenkins chạy `root` và
  có quyền điều khiển Docker của host. Chấp nhận được vì Jenkins chỉ chạy
  local; không dùng cách này trên máy chủ dùng chung.
- **Chỉ một workspace**: nhiều nhánh/PR build song song dùng chung
  `/srv/jenkins-ws/recsys-mlops`. `disableConcurrentBuilds` chỉ chặn cùng một
  nhánh. Với đồ án chỉ có `main` thì không ảnh hưởng; thêm nhánh thì phải xử lý
  lại.
- Cần dựng Jenkins bằng `-p mlops4rec-jenkins --env-file .env` (xem `README.md`),
  nếu không mount, mật khẩu admin và token Docker Hub không vào container.
- Build #7: 6/6 stage xanh trong khoảng 16,7 phút, 8 image đẩy lên Docker Hub
  với tag `87b0837`. Build #6 (cache trình build của Jenkins còn lạnh) mất
  khoảng 20 phút trong đó `docker compose up -d --build` chiếm 9,6 phút; ở
  build #7 bước này chỉ 1,5 phút và build 3 image test còn khoảng 1 phút (từ
  4,7 phút). Thời gian lúc này nằm ở `Build & Push Images` (khoảng 9,5
  phút).
- Sau đợt sửa 5–7 (commit `c7a0ba4`): build #10 xanh trong 27,6 phút — build
  đầu tiên dùng BuildKit và có `requirements/serving.txt` đổi nên build lại
  nhiều (khoảng 10 phút ở `Build test images`, `up --build` 4,8 phút) và
  đẩy lại layer (stage push 5,8 phút). Build #11 trên cùng commit (cache ấm):
  **xanh trong 6,8 phút**, 23 integration test 1,8 phút, Model Smoke Test
  khoảng 30 giây, không còn container `reco-ci-*` sau build. So với #6 (20 phút,
  fail) và #8 (11,4 phút).
- **Build #12** (commit `86591f8`, 2026-11-24, tự chạy nhờ `pollSCM`, khoảng 16
  phút): build đầu tiên chạy test của ADR 0013–0015 (49 + 15 unit test, 23
  integration, 5 test baseline trên MinIO, 5 model smoke) — tất cả pass. Push lỗi
  một image (`transform`: `proxyconnect ... i/o timeout` tới Docker Hub, 7/8 lên
  thành công) nhưng build vẫn **SUCCESS** vì `catchError(buildResult: 'SUCCESS')`.
  Đã đổi `buildResult` sang **`UNSTABLE`** (stage vẫn `FAILURE`): push hỏng không
  còn lẫn trong build xanh. Hệ quả: build có thể vàng khi mất mạng hoặc thiếu
  `DOCKERHUB_TOKEN`, thay vì xanh. Chưa chạy thật với giá trị mới và chưa qua lại
  Jenkins linter (cần đăng nhập); thay đổi chỉ là một giá trị hợp lệ của
  `buildResult`.
- **Build #13** (commit `552b6ab`, ~11 phút, tự chạy nhờ `pollSCM`): xanh, test như
  build #12 đều pass, **8/8 image lên Docker Hub tag `552b6ab`**; Jenkins đọc và chạy
  Jenkinsfile với `UNSTABLE` bình thường. Nhánh lỗi chưa bị kích hoạt (không có push
  nào lỗi), nên hành vi "build vàng" chưa được thấy tận mắt.
- Image Airflow vẫn được build 3 lần (3 service cùng Dockerfile, tên giống
  nhau); với BuildKit lần 2–3 là cache hit nên chưa xử lý riêng.

## Tham chiếu

`Jenkinsfile`, `infra/jenkins/docker-compose.jenkins.yml`,
`docs/decisions/0005-jenkins-thay-github-actions.md`,
`docs/modules/phase-8-cicd.md` (mục 2.x và 5), `docs/runbook.md`.
