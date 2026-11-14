# reco-mlops-libs

Package nội bộ dùng chung giữa các job (cài bằng `pip install -e ./libs`
trong mỗi image, không `COPY` thô — xem CLAUDE.md nguyên tắc #5). Nội dung
hiện có: kết nối Iceberg REST Catalog (`iceberg/catalog.py`), schema
contract + validator cho các bảng Bronze/Silver/Gold
(`iceberg/schema_contracts/`, `iceberg/validators.py`), helper đọc biến môi
trường (`common/env.py`), interface ranking model dùng chung cho tầng
serving (`ranking/base.py`), kiến trúc GRU4Rec dùng chung giữa training và
serving (`ranking/gru4rec.py` — cần `torch`, chỉ 2 image đó cài, không phải
dependency của `libs` nói chung), helper MLflow registry + lineage
(`mlflow_utils/`), model chính AGF-GRU4Rec (`ranking/agf/` — ADR 0008; kiến trúc,
quy tắc thuộc tính và vòng train chép từ repo thực nghiệm, cần `torch`). Xem `CHANGELOG.md` để biết chi tiết từng version.
