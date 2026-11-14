"""AGF (Adaptive Gated Fusion) — model chính từ ADR 0008.

- `agf_core.py`: kiến trúc (PyTorch thuần) — cần `torch`.
- `features.py`: quy tắc tạo thuộc tính item (numpy/pandas; text cần
  `sentence-transformers`, import muộn).
- `trainer.py`: vòng train/đánh giá/xuất artifact thuần PyTorch.

Package này cố ý không import gì ở `__init__` để image không có torch (vd
`transform`) vẫn import được `features` nếu cần.
"""
