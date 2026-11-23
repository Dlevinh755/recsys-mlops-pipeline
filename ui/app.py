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

st.set_page_config(page_title="Recommendation demo", page_icon="🎮", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,800&family=Instrument+Sans:wght@400;500;600&display=swap');
    :root {
        --paper: #0f1020; --surface: #181a33; --ink: #f2f2ff; --ink-soft: #9b9dcb;
        --violet: #7c5cff; --violet-deep: #6a48f0; --violet-soft: rgba(124,92,255,.22);
        --mint: #2dd4bf; --mint-soft: rgba(45,212,191,.12); --amber: #f59e0b;
        --line: #2b2d55; --night: #0a0b18; --night-2: #14162e; --night-line: #2b2d55;
        --display: 'Bricolage Grotesque', 'Segoe UI', sans-serif;
    }
    html, body, [class*="st-"], .stMarkdown {font-family: 'Instrument Sans', 'Segoe UI', sans-serif;}
    [data-testid="stIconMaterial"], [class*="material-symbols"], [class*="material-icons"] {
        font-family: 'Material Symbols Rounded', 'Material Icons' !important;
    }
    #MainMenu, footer, [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"] {visibility: hidden;}
    header[data-testid="stHeader"] {background: transparent;}
    .block-container {padding-top: 2rem; max-width: 1280px;}

    /* ---- sidebar: tối, có thẻ hồ sơ user ---- */
    section[data-testid="stSidebar"] > div {background: var(--night);}
    section[data-testid="stSidebar"] {border-right: 0;}
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding-top: 1.5rem;}
    [data-testid="stSidebar"] .stMarkdown, [data-testid="stSidebar"] p {color: #d9daf2;}
    [data-testid="stMain"] input::placeholder {color: var(--ink-soft);}
    [data-testid="stMain"] input {color: var(--ink);}
    .brand {display: flex; align-items: center; gap: 10px; margin-bottom: 1.6rem;}
    .brand .mark {width: 34px; height: 34px; border-radius: 10px; background: var(--violet);
                  display: grid; place-items: center; font-family: var(--display); font-weight: 800;
                  color: #fff; font-size: 1.05rem;}
    .brand .name {font-family: var(--display); font-weight: 800; font-size: 1.05rem;
                  letter-spacing: -.02em; color: #fff; line-height: 1.1;}
    .brand .tag {font-size: .72rem; color: #9b9dcb;}
    .side-label {font-size: .78rem; color: #9b9dcb; margin: 0 0 .5rem;}
    .profile {display: flex; align-items: center; gap: 12px; padding: 12px; margin-bottom: .75rem;
              background: var(--night-2); border: 1px solid var(--night-line); border-radius: 14px;}
    .avatar {width: 42px; height: 42px; border-radius: 50%; display: grid; place-items: center;
             font-family: var(--display); font-weight: 800; color: #fff; font-size: .95rem; flex: none;}
    .profile .pid {font-weight: 600; color: #fff; font-size: .95rem; line-height: 1.2;}
    .profile .pmeta {font-size: .72rem; color: #9b9dcb; word-break: break-all;}
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background: var(--night-2); border: 1px solid var(--night-line); border-radius: 12px;
        transition: border-color .15s;
    }
    [data-testid="stSidebar"] [data-baseweb="select"] > div:hover,
    [data-testid="stSidebar"] [data-baseweb="select"] > div:focus-within {border-color: var(--violet);}
    [data-testid="stSidebar"] [data-baseweb="select"] * {color: #fff !important;}
    [data-testid="stSidebar"] [data-baseweb="select"] svg {fill: #b9bbe6 !important;}
    .stat {margin-top: 1.25rem; padding: 14px; background: var(--night-2);
           border: 1px solid var(--night-line); border-radius: 14px;}
    .stat .k {font-size: .74rem; color: #9b9dcb; margin-bottom: 6px;}
    .pill {display: inline-flex; align-items: center; gap: 7px; padding: 4px 12px; border-radius: 999px;
           font-size: .78rem; font-weight: 600;}
    .pill::before {content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor;}
    .pill.model {background: rgba(45,212,191,.16); color: #5eead4;}
    .pill.fallback {background: rgba(245,158,11,.16); color: #fbbf24;}
    .stat .d {font-size: .78rem; color: #b6b8dd; margin-top: 8px; line-height: 1.4;}

    /* ---- trang chính ---- */
    .masthead {display: flex; align-items: baseline; justify-content: space-between;
               gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem;}
    .masthead h1 {font-family: var(--display); font-weight: 800; font-size: 2.6rem;
                  letter-spacing: -.035em; line-height: 1; margin: 0; padding: 0;}
    .masthead .who {color: var(--ink-soft); font-size: .95rem;}
    .masthead .who b {color: var(--ink); font-weight: 600;}

    [data-testid="stMain"] [data-baseweb="input"] {background: var(--surface); border: 1px solid var(--line);
        border-radius: 12px;}
    [data-testid="stMain"] [data-baseweb="input"]:focus-within {border-color: var(--violet);}

    [class*="st-key-card-"] {
        background: var(--surface); border: 1px solid var(--line); border-radius: 18px;
        box-shadow: 0 1px 2px rgba(0,0,0,.3);
        transition: border-color .2s, box-shadow .2s, transform .2s;
    }
    [class*="st-key-card-"]:hover {
        border-color: var(--violet); box-shadow: 0 16px 36px rgba(124,92,255,.28); transform: translateY(-3px);
    }

    .thumb {position: relative; aspect-ratio: 4 / 5; display: flex; align-items: center;
            justify-content: center; background: #eceefa; border-radius: 14px;
            overflow: hidden; padding: 38px 12px 12px;}
    .thumb img {max-width: 100%; max-height: 100%; object-fit: contain; mix-blend-mode: multiply;}
    .rank {position: absolute; left: 12px; top: 6px; font-family: var(--display); font-weight: 800;
           font-size: 2.4rem; line-height: 1; color: var(--violet); letter-spacing: -.04em;}
    .title {font-family: var(--display); font-weight: 600; font-size: 1rem; line-height: 1.25;
            letter-spacing: -.01em; margin: .8rem 0 .3rem; display: -webkit-box;
            -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 2.5em;}
    .cat {font-size: .78rem; color: var(--ink-soft); margin-bottom: .6rem;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
    .price {font-family: var(--display); font-weight: 800; font-size: 1.35rem; letter-spacing: -.02em;}
    .bar {height: 6px; background: var(--violet-soft); border-radius: 999px; margin: .55rem 0 .25rem;}
    .bar i {display: block; height: 100%; background: var(--violet); border-radius: 999px;}
    .bar-label {font-size: .72rem; color: var(--ink-soft); margin-bottom: .6rem;}

    .feature .title {font-size: 1.9rem; line-height: 1.12; letter-spacing: -.025em; min-height: 0;
                     -webkit-line-clamp: 3; margin-top: .2rem;}
    .feature .thumb {padding-top: 76px;}
    .feature .rank {font-size: 4.4rem; top: 0; left: 16px;}
    .feature .price {font-size: 2.2rem;}
    .feature .cat {font-size: .9rem;}
    .pick-note {font-size: .85rem; color: var(--ink-soft); margin: .2rem 0 1rem; max-width: 34ch;}

    [class*="st-key-card-"] button[data-testid="stBaseButton-secondary"] {
        border-radius: 12px; border: 0 !important;
        background: var(--violet) !important; color: #fff !important;
        font-weight: 600; transition: background .15s;
    }
    [class*="st-key-card-"] button[data-testid="stBaseButton-secondary"] p {color: inherit !important;}
    [class*="st-key-card-"] button[data-testid="stBaseButton-secondary"]:hover {
        background: var(--violet-deep) !important;
    }

    .panel-title {font-family: var(--display); font-weight: 800; font-size: 1.15rem;
                  letter-spacing: -.02em; margin: 0 0 .75rem;}
    .hist-row {display: flex; gap: 10px; align-items: center; background: var(--surface);
               border-radius: 14px; padding: 8px; margin-bottom: 8px; border: 1px solid var(--line);}
    .hist-row.session {border-color: rgba(45,212,191,.5); background: var(--mint-soft);}
    .hist-row img {width: 44px; height: 44px; object-fit: contain; flex: none;
                   background: #eceefa; border-radius: 10px; padding: 3px;}
    .hist-title {font-size: .8rem; font-weight: 600; line-height: 1.25; display: -webkit-box;
                 -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;}
    .hist-meta {font-size: .72rem; color: var(--ink-soft);}
    .hist-meta span + span {margin-left: .6em;}

    @media (prefers-reduced-motion: reduce) {
        [class*="st-key-card-"], .stButton button {transition: none; transform: none !important;}
    }
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
        else '<span style="width:44px;text-align:center">🎮</span>'
    )
    when = "phiên này" if entry["source"] == "session" else (entry.get("event_time") or "")[:10]
    meta = "".join(f"<span>{html.escape(part)}</span>" for part in (price, when) if part)
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


def search_products(query: str) -> list[dict]:
    response = requests.get(
        f"{SERVING_BASE_URL}/products/search", params={"q": query, "limit": 12}, timeout=10
    )
    response.raise_for_status()
    return response.json()["items"]


def render_card(
    item: dict, user_id: str, rank: int | None, top_score: float, feature: bool = False
) -> None:
    """`rank=None` là chế độ kết quả tìm kiếm: không số thứ tự, không thanh điểm
    (kết quả sắp theo độ phổ biến, không có điểm model)."""
    category, price = split_description(item.get("description"))
    title = html.escape(item.get("title") or item["product_id"])
    image = (
        f'<img src="{html.escape(item["image_url"], quote=True)}" loading="lazy">'
        if item.get("image_url")
        else '<span style="font-size:2.5rem">🎮</span>'
    )
    share = max(0.0, min(1.0, item["score"] / top_score)) if top_score > 0 else 0.0
    rank_tag = f'<span class="rank">{rank}</span>' if rank is not None else ""
    thumb = f'<div class="thumb">{rank_tag}{image}</div>'
    score_block = (
        f'<div class="bar"><i style="width:{share * 100:.0f}%"></i></div>'
        f'<div class="bar-label">Điểm {item["score"]:.2f}, bằng {share * 100:.0f}% điểm cao nhất</div>'
        if rank is not None
        else ""
    )
    details = (
        f'<div class="title" title="{title}">{title}</div>'
        f'<div class="cat">{html.escape(category)}</div>'
        f'<div class="price">{html.escape(price)}</div>'
        f"{score_block}"
    )
    with st.container(border=True, key=f"card-{item['product_id']}"):
        if feature:
            image_col, text_col = st.columns([1, 1.3], gap="large")
            with image_col:
                st.markdown(f'<div class="feature">{thumb}</div>', unsafe_allow_html=True)
            with text_col:
                st.markdown(
                    f'<div class="feature">{details}</div>'
                    '<div class="pick-note">Món hợp nhất với chuỗi hành vi gần đây của user này.</div>',
                    unsafe_allow_html=True,
                )
                clicked = st.button("Đã mua", key=f"buy-{item['product_id']}", use_container_width=True)
        else:
            st.markdown(thumb + details, unsafe_allow_html=True)
            clicked = st.button("Đã mua", key=f"buy-{item['product_id']}", use_container_width=True)
        if clicked:
            send_interact(user_id, item["product_id"])
            st.toast("Đã ghi nhận mua, đang cập nhật gợi ý…", icon="✅")
            st.rerun()


try:
    users = fetch_users()
except requests.RequestException as error:
    st.error(f"Không gọi được serving ({SERVING_BASE_URL}): {error}")
    st.stop()

if not users:
    st.warning("Chưa có user nào trong gold.user_features — chạy pipeline Phase 2/3 trước.")
    st.stop()

AVATAR_COLORS = ("#7c5cff", "#14b8a6", "#f43f5e", "#f59e0b", "#0ea5e9", "#8b5cf6")

with st.sidebar:
    st.markdown(
        '<div class="brand"><div class="mark">R</div><div>'
        '<div class="name">Recommendation</div><div class="tag">demo gợi ý sản phẩm</div></div></div>'
        '<div class="side-label">Đang xem với tư cách</div>',
        unsafe_allow_html=True,
    )
    profile_slot = st.empty()
    user_id = st.selectbox(
        "Chọn user", [u["user_id"] for u in users], format_func=short_id, label_visibility="collapsed"
    )
    initials = html.escape(user_id[:2].upper())
    color = AVATAR_COLORS[sum(map(ord, user_id)) % len(AVATAR_COLORS)]
    profile_slot.markdown(
        f'<div class="profile"><div class="avatar" style="background:{color}">{initials}</div><div>'
        f'<div class="pid">{html.escape(short_id(user_id))}</div>'
        f'<div class="pmeta">{html.escape(user_id)}</div></div></div>',
        unsafe_allow_html=True,
    )

try:
    homepage = fetch_homepage(user_id)
except requests.RequestException as error:
    st.error(f"Không gọi được serving ({SERVING_BASE_URL}): {error}")
    st.stop()

is_model = homepage["source"] == "model"
with st.sidebar:
    source_note = (
        "AGF-GRU4Rec chấm điểm theo chuỗi hành vi + thuộc tính sản phẩm."
        if is_model
        else "Chưa đủ lịch sử, đang hiện sản phẩm phổ biến."
    )
    st.markdown(
        '<div class="stat"><div class="k">Nguồn gợi ý</div>'
        f'<span class="pill {"model" if is_model else "fallback"}">{html.escape(homepage["source"])}</span>'
        f'<div class="d">{source_note}</div></div>',
        unsafe_allow_html=True,
    )

items = sorted(homepage["items"], key=lambda item: item["score"], reverse=True)
st.markdown(
    f'<div class="masthead"><h1>Bảng xếp hạng gợi ý</h1>'
    f'<div class="who">cho <b>{html.escape(short_id(user_id))}</b>, {len(items)} sản phẩm</div></div>',
    unsafe_allow_html=True,
)

grid_col, history_col = st.columns([3.2, 1], gap="large")

with history_col:
    st.markdown('<div class="panel-title">Đã tương tác</div>', unsafe_allow_html=True)
    history_items = fetch_history(user_id)
    if history_items:
        st.markdown("".join(render_history_row(e) for e in history_items), unsafe_allow_html=True)
    else:
        st.caption("Chưa có lịch sử.")

with grid_col:
    query = st.text_input(
        "Tìm sản phẩm", placeholder="Tìm theo tên hoặc danh mục, ví dụ: zelda, controller", label_visibility="collapsed"
    ).strip()
    if query:
        try:
            found = search_products(query)
        except requests.RequestException as error:
            st.error(f"Không tìm được sản phẩm: {error}")
            found = []
        st.markdown(
            f'<div class="panel-title">{len(found)} kết quả cho “{html.escape(query)}”</div>',
            unsafe_allow_html=True,
        )
        if not found:
            st.caption("Không có sản phẩm khớp. Thử từ khoá ngắn hơn hoặc tên khác.")
        for start in range(0, len(found), GRID_COLUMNS):
            columns = st.columns(GRID_COLUMNS)
            for column, item in zip(columns, found[start : start + GRID_COLUMNS]):
                with column:
                    render_card(item, user_id, None, 0.0)
    elif items:
        top_score = max(item["score"] for item in items)
        render_card(items[0], user_id, 1, top_score, feature=True)
        rest = items[1:]
        for start in range(0, len(rest), GRID_COLUMNS):
            columns = st.columns(GRID_COLUMNS)
            for offset, (column, item) in enumerate(zip(columns, rest[start : start + GRID_COLUMNS])):
                with column:
                    render_card(item, user_id, start + offset + 2, top_score)
