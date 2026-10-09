# MLOPS4REC — Recommendation MLOps Pipeline

Khóa luận tốt nghiệp: mô phỏng đầy đủ vòng đời MLOps (ingest → lakehouse →
feature store → train → registry → serve → monitor → feedback loop) chạy
hoàn toàn local, quy mô MVP.

Code nằm ở thư mục gốc repo — mọi lệnh
`make`/`docker compose` phải chạy từ thư mục đó.

## Đọc gì trước khi code

Đừng tự suy luận kiến trúc — mọi quyết định đã có tài liệu, đọc theo thứ tự
ưu tiên sau:

1. **`docs/STATUS.md`** — đang ở phase nào, việc gì vừa xong, việc gì tiếp
   theo. Đọc đầu tiên trong mọi phiên làm việc.
2. **`docs/de-xuat-trien-khai.md`** — checklist chi tiết + Definition of Done
   của từng phase (0-9). Đây là nguồn kế hoạch, không phải nguồn sự thật về
   những gì đã code xong.
3. **`docs/modules/phase-N-*.md`** — tài liệu "as-built" ghi lại **thực tế**
   đã triển khai cho phase N, kể cả chỗ lệch so với kế hoạch. Đây mới là
   nguồn sự thật về code hiện có. Nếu `docs/modules/` và
   `de-xuat-trien-khai.md` mâu thuẫn nhau, tin `docs/modules/`.
4. **`docs/bao-cao-ky-thuat.md`** — lý do chọn công nghệ, ranh giới phạm vi,
   nguyên tắc kiến trúc cốt lõi (mục 10.1). Đọc khi cần hiểu "tại sao lại làm
   thế này" thay vì "làm gì".
5. **`docs/decisions/`** — ADR cho từng quyết định lệch khỏi tài liệu gốc
   (đổi công nghệ, đổi thứ tự phase, bỏ bớt việc). Đọc khi thấy code không
   khớp với `bao-cao-ky-thuat.md`/`de-xuat-trien-khai.md` — thường đã có ADR
   giải thích.
6. **`docs/cau-truc-project.md`** — sơ đồ thư mục đầy đủ dự kiến của toàn bộ
   project và lý do chia như vậy. Dùng để biết một file mới nên nằm ở đâu.

## Nguyên tắc kiến trúc bắt buộc tuân thủ

Trích từ `bao-cao-ky-thuat.md` mục 10.1 — không tự ý phá vỡ khi sinh code
mới, kể cả khi có vẻ tiện hơn ở phạm vi nhỏ:

1. **Airflow chỉ orchestration.** DAG chỉ gọi `python -m jobs.xxx.run`,
   không chứa business logic. Mỗi job phải chạy và test được độc lập qua
   `scripts/run_job_locally.sh` trước khi có DAG gọi tới.
2. **Mỗi nhóm job = 1 Docker image riêng** (extract, transform, training,
   serving, monitoring, airflow, ui), build qua `COPY`, **không bind-mount**
   code ở môi trường production-like.
3. **Iceberg là source of truth duy nhất** giữa các tầng batch. Không tầng
   nào gọi thẳng API nội bộ của tầng khác. Ngoại lệ duy nhất đã ghi nhận:
   `POST /interact` ghi thẳng Redis + source-db (xem mục 8.1 báo cáo kỹ
   thuật) — không tạo thêm ngoại lệ tương tự nếu chưa có ADR.
4. **Mỗi bảng có schema contract tường minh** (`libs/iceberg/
   schema_contracts/`) + validate bằng `pandera` trước khi commit Iceberg
   (`libs/iceberg/validators.py`). Không commit dữ liệu chưa qua validator.
5. **`libs/` là package versioned** (`reco_mlops_libs`, cài qua `-e ../libs`
   trong `requirements/<job>.txt`), không `COPY` thô mã nguồn dùng chung vào
   từng image. Mỗi lần sửa `libs/` phải tăng version + ghi `libs/CHANGELOG.md`.
6. **Không lưu state quan trọng trên filesystem cục bộ của container.** Mọi
   dữ liệu bền vững đi qua MinIO/Iceberg/MLflow artifact store/Redis/Postgres.
7. **Model chỉ được gọi qua interface `predict(user_id, candidate_items) ->
   scores`** (`libs/ranking/base.py`, thêm từ Phase 4) — không gọi thẳng
   `model.predict(dataframe)` ở tầng API.
8. Mỗi image gắn tag `git rev-parse --short HEAD`.

## Quy ước bắt buộc khi hoàn thành một phase

Không được coi một phase là "xong" nếu thiếu bất kỳ bước nào dưới đây —
đây là cơ chế chính để tránh tình trạng code xong nhưng không ai (kể cả
Claude Code ở phiên sau) hiểu rõ đã làm gì và tại sao:

1. **Đối chiếu Definition of Done** của phase đó trong
   `de-xuat-trien-khai.md` trước khi tuyên bố hoàn thành.
2. **Viết/cập nhật `docs/modules/phase-N-<ten>.md`** theo đúng khung mẫu đã
   dùng ở Phase 0-3: Mục tiêu ban đầu → Đã triển khai (map trực tiếp vào
   file code, có bảng) → Khác biệt so với đề xuất ban đầu → Đối chiếu
   Definition of Done → Việc còn để lại cho phase sau → **Danh sách file đã
   triển khai** (bảng `Đường dẫn | Vai trò | Config/setting đáng chú ý`,
   nhóm theo loại: code chính, hạ tầng/Docker, SQL, test/script — xem
   Phase 0-3 làm mẫu). Mục danh sách file là để tra cứu nhanh khi đọc lại
   code sau này, không lặp lại phần diễn giải đã có ở "Đã triển khai" —
   chỉ 1 dòng/file. Viết **sau khi** code xong, phản ánh thực tế — không
   viết trước rồi code theo cho khớp.
3. **Nếu có bất kỳ lựa chọn nào lệch khỏi `bao-cao-ky-thuat.md` hoặc
   `de-xuat-trien-khai.md`** (đổi công nghệ, đổi thứ tự, bỏ bớt việc, thêm
   ngoại lệ vào nguyên tắc ở trên) → thêm một file ADR mới trong
   `docs/decisions/` (đánh số tiếp theo, xem `docs/decisions/0001-*.md` làm
   mẫu) **trước khi** coi phase xong. Không chỉ nhắc trong module doc.
4. **Cập nhật `docs/STATUS.md`** — chuyển phase vừa xong sang "Hoàn thành",
   phase tiếp theo sang "Đang làm"/"Kế tiếp".
5. Mỗi checklist item trong `de-xuat-trien-khai.md` nên là một commit/PR
   riêng, không gộp cả phase vào một commit (đặc biệt Phase 2 — Iceberg —
   dễ cần bisect lại nếu gộp).

## Lệnh hay dùng

Chạy từ thư mục gốc repo:

```bash
make up          # dựng hạ tầng nền (Phase 0)
make validate    # kiểm tra cấu trúc + compose config
make test        # unit test Python
make smoke       # kiểm tra service đã lên + seed data
make extract     # chạy job extract Phase 1 ngoài Airflow
```

## Không làm

- Không thêm dependency/service mới vào `docker-compose.yml` chính nếu nó
  chỉ phục vụ observability/demo — dùng `docker-compose.override.yml`
  (Prometheus/Grafana đã có chỗ để dành, xem Phase 7).
- Không viết tài liệu `docs/` mô tả tính năng **trước khi** có code — gây
  lệch giữa tài liệu và thực tế (nguyên tắc đã ghi trong Phase 8 DoD).
- Không tự thêm ADR cho những quyết định đã nằm sẵn trong
  `de-xuat-trien-khai.md`/`bao-cao-ky-thuat.md` — ADR chỉ dành cho chỗ
  **lệch** so với tài liệu gốc.
