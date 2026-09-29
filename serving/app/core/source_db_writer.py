"""Nhánh (B) của `POST /interact` (`docs/bao-cao-ky-thuat.md` mục 8.1): ghi
1 dòng vào bảng `interactions` của `source-db` — đúng bảng, đúng cơ chế
watermark (`dlt.sources.incremental`, ADR 0006) mà `extract-job` đã đọc,
không có đường xử lý riêng nào cho dữ liệu tới từ serving.

`created_at`/`updated_at` dùng `DEFAULT CURRENT_TIMESTAMP` của chính
Postgres (không set thủ công từ Python) — nhất quán với cách
`event_time` cũng lấy `CURRENT_TIMESTAMP` tại thời điểm ghi.
"""

from __future__ import annotations

import psycopg


class UnknownEntityError(Exception):
    """`user_id`/`product_id` không tồn tại — tầng API biến lỗi này thành
    404/422 rõ ràng thay vì để lộ ra một FK violation thô từ Postgres."""


def _exists(conn: psycopg.Connection, table: str, column: str, value: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE {column} = %s", (value,)).fetchone()  # noqa: S608
    return row is not None


def write_interaction(
    conn: psycopg.Connection,
    user_id: str,
    product_id: str,
    event_type: str,
    rating: float | None = None,
) -> None:
    if not _exists(conn, "users", "user_id", user_id):
        raise UnknownEntityError(f"user_id {user_id!r} does not exist")
    if not _exists(conn, "products", "product_id", product_id):
        raise UnknownEntityError(f"product_id {product_id!r} does not exist")
    conn.execute(
        """
        INSERT INTO interactions (user_id, product_id, event_type, rating, event_time)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """,
        (user_id, product_id, event_type, rating),
    )
    conn.commit()
