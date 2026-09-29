"""Phase 5 demo UI — `docs/bao-cao-ky-thuat.md` mục 8.2. A standalone
Streamlit app calling `serving` over plain HTTP; not part of the business
pipeline, shares no code/image with `serving/` (see
`docs/modules/phase-5-serving.md`) — can be deleted entirely without
affecting anything else.
"""

from __future__ import annotations

import html
import os

import requests
import streamlit as st

SERVING_BASE_URL = os.environ.get("SERVING_BASE_URL", "http://localhost:8000")
GRID_COLUMNS = 3

st.set_page_config(page_title="Recommendation demo", page_icon="🛍️", layout="wide")

st.markdown(
    """
    <style>
    #MainMenu, footer {visibility: hidden;}
    .block-container {padding-top: 2rem; max-width: 1200px;}
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: #fff; border-radius: 14px; transition: box-shadow .15s, transform .15s;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        box-shadow: 0 6px 18px rgba(31,41,51,.10); transform: translateY(-2px);
    }
    .thumb {aspect-ratio: 1 / 1; display: flex; align-items: center; justify-content: center;
            background: #fff; border-radius: 10px; overflow: hidden;}
    .thumb img {max-width: 100%; max-height: 100%; object-fit: contain;}
    .title {font-weight: 600; font-size: .92rem; line-height: 1.3; margin: .6rem 0 .4rem;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
            overflow: hidden; min-height: 2.4em;}
    .chip {display: inline-block; font-size: .72rem; color: #52606d; background: #eef1f4;
           border-radius: 999px; padding: 2px 10px; margin-right: 6px;}
    .price {font-size: 1.15rem; font-weight: 700; color: #e8590c;}
    .meta {font-size: .72rem; color: #9aa5b1; margin-top: .2rem;}
    .hist-row {display: flex; gap: 10px; align-items: center; background: #fff; border-radius: 10px;
               padding: 8px; margin-bottom: 8px; border: 1px solid #e4e7eb;}
    .hist-row.session {border-color: #e8590c; background: #fff4e6;}
    .hist-row img {width: 48px; height: 48px; object-fit: contain; flex: none;}
    .hist-title {font-size: .78rem; font-weight: 600; line-height: 1.25; display: -webkit-box;
                 -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;}
    .hist-meta {font-size: .7rem; color: #7b8794;}
    .badge {display: inline-block; padding: 3px 12px; border-radius: 999px; font-size: .8rem;
            font-weight: 600; color: #fff;}
    </style>
    """,
    unsafe_allow_html=True,
)


def fetch_users() -> list[dict]:
    response = requests.get(f"{SERVING_BASE_URL}/users", timeout=10)
    response.raise_for_status()
    return response.json()["users"]


def fetch_homepage(user_id: str) -> dict:
    response = requests.get(
        f"{SERVING_BASE_URL}/recommend/homepage", params={"user_id": user_id}, timeout=10
    )
    response.raise_for_status()
    return response.json()


def send_interact(user_id: str, item_id: str) -> None:
    response = requests.post(
        f"{SERVING_BASE_URL}/interact",
        json={"user_id": user_id, "item_id": item_id, "event_type": "purchase"},
        timeout=10,
    )
    response.raise_for_status()


def fetch_history(user_id: str) -> list[dict]:
    try:
        response = requests.get(f"{SERVING_BASE_URL}/users/{user_id}/history", timeout=10)
        response.raise_for_status()
        return response.json()["items"]
    except requests.RequestException:
        return []


def render_history_row(entry: dict) -> str:
    _, price = split_description(entry.get("description"))
    title = html.escape(entry.get("title") or entry["product_id"])
    image = (
        f'<img src="{html.escape(entry["image_url"], quote=True)}" loading="lazy">'
        if entry.get("image_url")
        else '<span style="width:48px;text-align:center">🛍️</span>'
    )
    when = "phiên này" if entry["source"] == "session" else (entry.get("event_time") or "")[:10]
    meta = " · ".join(part for part in (html.escape(price), html.escape(when)) if part)
    css = "hist-row session" if entry["source"] == "session" else "hist-row"
    return (
        f'<div class="{css}">{image}<div><div class="hist-title" title="{title}">{title}</div>'
        f'<div class="hist-meta">{meta}</div></div></div>'
    )


def short_id(user_id: str) -> str:
    return user_id if len(user_id) <= 14 else f"{user_id[:6]}…{user_id[-4:]}"


def split_description(description: str | None) -> tuple[str, str]:
    """`serving` ghép `"<category> · $<price>"` — tách lại để hiển thị giá
    nổi bật; nếu không đúng dạng đó thì coi cả chuỗi là danh mục."""
    if not description:
        return "", ""
    category, sep, price = description.rpartition(" · ")
    return (category, price) if sep and price.startswith("$") else (description, "")


def render_card(item: dict, user_id: str) -> None:
    category, price = split_description(item.get("description"))
    title = html.escape(item.get("title") or item["product_id"])
    image = (
        f'<img src="{html.escape(item["image_url"], quote=True)}" loading="lazy">'
        if item.get("image_url")
        else '<span style="font-size:2.5rem">🛍️</span>'
    )
    with st.container(border=True):
        st.markdown(
            f'<div class="thumb">{image}</div>'
            f'<div class="title" title="{title}">{title}</div>'
            f'<span class="chip">{html.escape(category)}</span>'
            f'<div class="price">{html.escape(price)}</div>'
            f'<div class="meta">{html.escape(item["product_id"])} · score {item["score"]:.2f}</div>',
            unsafe_allow_html=True,
        )
        if st.button("Đã mua", key=f"buy-{item['product_id']}", use_container_width=True):
            send_interact(user_id, item["product_id"])
            st.toast("Đã ghi nhận mua — đang cập nhật gợi ý…", icon="✅")
            st.rerun()


try:
    users = fetch_users()
except requests.RequestException as error:
    st.error(f"Không gọi được serving ({SERVING_BASE_URL}): {error}")
    st.stop()

if not users:
    st.warning("Chưa có user nào trong gold.user_features — chạy pipeline Phase 2/3 trước.")
    st.stop()

with st.sidebar:
    st.header("🛍️ Recommendation demo")
    user_id = st.selectbox("Chọn user", [u["user_id"] for u in users], format_func=short_id)
    st.caption(f"`{user_id}`")

try:
    homepage = fetch_homepage(user_id)
except requests.RequestException as error:
    st.error(f"Không gọi được serving ({SERVING_BASE_URL}): {error}")
    st.stop()

is_model = homepage["source"] == "model"
with st.sidebar:
    st.markdown(
        f'Nguồn gợi ý: <span class="badge" style="background:{"#2f9e44" if is_model else "#f08c00"}">'
        f'{html.escape(homepage["source"])}</span>',
        unsafe_allow_html=True,
    )
    st.caption(
        "GRU4Rec chấm điểm theo chuỗi hành vi." if is_model else "Chưa đủ lịch sử — hiện sản phẩm phổ biến."
    )

st.subheader(f"Gợi ý cho {short_id(user_id)}")

items = homepage["items"]
grid_col, history_col = st.columns([3, 1], gap="large")

with history_col:
    st.markdown("##### 🕘 Lịch sử tương tác")
    history_items = fetch_history(user_id)
    if history_items:
        st.markdown("".join(render_history_row(e) for e in history_items), unsafe_allow_html=True)
    else:
        st.caption("Chưa có lịch sử.")

with grid_col:
    for start in range(0, len(items), GRID_COLUMNS):
        columns = st.columns(GRID_COLUMNS)
        for column, item in zip(columns, items[start : start + GRID_COLUMNS]):
            with column:
                render_card(item, user_id)
