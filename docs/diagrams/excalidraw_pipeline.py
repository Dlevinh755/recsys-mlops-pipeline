"""Sinh sơ đồ pipeline MLOPS4REC dạng Excalidraw (nét vẽ tay, 4 cột, có logo).

Chạy từ thư mục gốc repo (cần `pip install diagrams` để lấy logo có sẵn):
    python docs/diagrams/excalidraw_pipeline.py

Kết quả: docs/diagrams/out/mlops4rec_pipeline.excalidraw
→ mở bằng excalidraw.com (Open / Ctrl+O) hoặc extension Excalidraw trong VS Code.

Logo: ưu tiên file docs/diagrams/icons/<key>.png (vd. icons/minio.png,
icons/evidently.png); không có thì lấy PNG trong package `diagrams`; không có
nữa thì bỏ trống chỗ logo (script in ra danh sách logo còn thiếu).
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import random
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICON_DIR = HERE / "icons"
OUT = HERE / "out" / "mlops4rec_pipeline.excalidraw"

# key -> các class trong `diagrams` có thể chứa logo đó
DIAGRAMS_ICONS = {
    "postgres": ["diagrams.onprem.database:PostgreSQL"],
    "redis": ["diagrams.onprem.inmemory:Redis"],
    "airflow": ["diagrams.onprem.workflow:Airflow"],
    "mlflow": ["diagrams.onprem.mlops:Mlflow"],
    "github": ["diagrams.onprem.vcs:Github"],
    "jenkins": ["diagrams.onprem.ci:Jenkins"],
    "docker": ["diagrams.onprem.container:Docker"],
    "fastapi": ["diagrams.programming.framework:Fastapi", "diagrams.programming.framework:FastAPI"],
    "prometheus": ["diagrams.onprem.monitoring:Prometheus"],
    "grafana": ["diagrams.onprem.monitoring:Grafana"],
    "minio": ["diagrams.onprem.storage:Minio", "diagrams.onprem.storage:MinIO"],
    "pytorch": ["diagrams.programming.framework:Pytorch", "diagrams.programming.framework:PyTorch"],
}

INK = "#1e1e1e"
OPS = "#868e96"
FEEDBACK = "#e8590c"
FONT = 5  # Excalifont — nét chữ viết tay như hình mẫu
LOGO = 86  # cạnh ô logo (px)
WIDEN_AT, WIDEN_BY = 860, 30  # nới cột Training để đủ chỗ cho logo lớn

random.seed(42)
elements: list[dict] = []
files: dict[str, dict] = {}
missing: list[str] = []


# ------------------------------------------------------------------ logos
def _png_from_diagrams(key: str) -> bytes | None:
    for path in DIAGRAMS_ICONS.get(key, []):
        module, cls_name = path.split(":")
        try:
            cls = getattr(importlib.import_module(module), cls_name)
            pkg_root = Path(importlib.import_module("diagrams").__file__).resolve().parent.parent
            png = pkg_root / cls._icon_dir / cls._icon
            if png.exists():
                return png.read_bytes()
        except Exception:  # noqa: BLE001 — thiếu package/class thì thử tiếp
            continue
    return None


def load_logo(key: str) -> bytes | None:
    local = ICON_DIR / f"{key}.png"
    if local.exists():
        return local.read_bytes()
    return _png_from_diagrams(key)


# --------------------------------------------------------------- elements
def _base(kind: str, x: float, y: float, w: float, h: float, **kw) -> dict:
    el = {
        "id": f"{kind}-{len(elements)}", "type": kind, "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": INK, "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1, "opacity": 100,
        "groupIds": [], "frameId": None, "roundness": None, "seed": random.randint(1, 2**31),
        "version": 1, "versionNonce": random.randint(1, 2**31), "isDeleted": False,
        "boundElements": [], "updated": 1, "link": None, "locked": False,
    }
    el.update(kw)
    elements.append(el)
    return el


def _text_size(s: str, size: int) -> tuple[float, float]:
    lines = s.split("\n")
    return max(len(l) for l in lines) * size * 0.55, len(lines) * size * 1.25


def text(x: float, y: float, s: str, size: int = 16, color: str = INK, center: bool = False) -> dict:
    w, h = _text_size(s, size)
    if center:
        x -= w / 2
    return _base("text", x, y, w, h, strokeColor=color, text=s, originalText=s, fontSize=size,
                 fontFamily=FONT, textAlign="center" if center else "left", verticalAlign="top",
                 containerId=None, lineHeight=1.25, autoResize=True)


def box(x: float, y: float, w: float, h: float, label: str = "", *, shape: str = "rectangle",
        bg: str = "#ffffff", dashed: bool = False, size: int = 16, color: str = INK) -> dict:
    el = _base(shape, x, y, w, h, strokeColor=color, backgroundColor=bg,
               strokeStyle="dashed" if dashed else "solid",
               roundness={"type": 2 if shape == "diamond" else 3})
    if label:
        tw, th = _text_size(label, size)
        t = _base("text", x + (w - tw) / 2, y + (h - th) / 2, tw, th, strokeColor=color,
                  text=label, originalText=label, fontSize=size, fontFamily=FONT,
                  textAlign="center", verticalAlign="middle", containerId=el["id"],
                  lineHeight=1.25, autoResize=True)
        el["boundElements"].append({"type": "text", "id": t["id"]})
    return el


def logo(key: str, x: float, y: float, size: float = LOGO) -> None:
    y -= (size - 66) / 2  # y truyền vào tính theo ô 66px cũ; giữ tâm logo ngang tâm box
    data = load_logo(key)
    if data is None:
        missing.append(key)
        return
    w, h = struct.unpack(">II", data[16:24])
    scale = size / max(w, h)
    dw, dh = w * scale, h * scale
    file_id = hashlib.sha1(data).hexdigest()
    files[file_id] = {"mimeType": "image/png", "id": file_id, "created": 1, "lastRetrieved": 1,
                      "dataURL": "data:image/png;base64," + base64.b64encode(data).decode()}
    _base("image", x + (size - dw) / 2, y + (size - dh) / 2, dw, dh, fileId=file_id,
          status="saved", scale=[1, 1], crop=None)


def arrow(points: list[tuple[float, float]], start: dict | None = None, end: dict | None = None,
          *, color: str = INK, dashed: bool = False, both: bool = False) -> dict:
    x0, y0 = points[0]
    rel = [[px - x0, py - y0] for px, py in points]
    xs, ys = [p[0] for p in rel], [p[1] for p in rel]
    el = _base("arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys), strokeColor=color,
               strokeStyle="dashed" if dashed else "solid", roughness=0, points=rel,
               lastCommittedPoint=None, elbowed=False,
               startBinding={"elementId": start["id"], "focus": 0, "gap": 3} if start else None,
               endBinding={"elementId": end["id"], "focus": 0, "gap": 3} if end else None,
               startArrowhead="arrow" if both else None, endArrowhead="arrow")
    for b in (start, end):
        if b is not None:
            b["boundElements"].append({"type": "arrow", "id": el["id"]})
    return el


def down(a: dict, b: dict, x: float, **kw) -> dict:
    """Mũi tên thẳng đứng từ đáy a xuống đỉnh b tại hoành độ x."""
    return arrow([(x, a["y"] + a["height"]), (x, b["y"])], a, b, **kw)


def column(x: float, w: float, title: str, bg: str) -> None:
    _base("rectangle", x, 65, w, 935, backgroundColor=bg, roundness={"type": 3},
          strokeColor="#495057")
    text(x + w / 2, 72, title, 20, center=True)


# ------------------------------------------------------------------ layout
def build() -> None:
    _base("rectangle", -15, -10, 1800, 1065, strokeStyle="dashed", roundness=None,
          strokeColor="#495057")
    text(885, -2, "MLOPS4REC — END-TO-END MLOPS PIPELINE FOR SEQUENTIAL RECOMMENDATION "
                  "(AGF-GRU4Rec, AMAZON CELL PHONES)", 20, center=True)

    column(0, 420, "Data_And_Lakehouse", "#f0fbf8")
    column(440, 420, "Training_And_Registry", "#f5f2ff")
    column(880, 420, "Serving_And_Deployment", "#eef7fc")
    column(1320, 450, "Monitoring_And_Feedback", "#fff6f0")

    # --- A. Data & lakehouse (logo bên trái, box x=100..360, tâm 230)
    src = box(100, 105, 260, 60, "source-db (Postgres)\nusers · products · interactions")
    ext = box(100, 195, 260, 60, "extract-job\ndlt incremental + watermark")
    stg = box(100, 285, 260, 60, "MinIO extract-staging\nParquet files")
    box(90, 375, 280, 275, dashed=True, bg="transparent", color="#495057")
    text(230, 382, "Iceberg tables · Lakekeeper catalog", 15, center=True)
    bronze = box(110, 415, 240, 50, "Bronze — append")
    silver = box(110, 495, 240, 50, "Silver — dedup + pandera")
    gold = box(110, 575, 240, 60, "Gold\nuser_features · item_features")
    feast = box(100, 695, 260, 60, "materialize-job (Feast)")
    redis = box(100, 785, 260, 60, "Redis online store\nrecent_items · similar_items")
    for key, y in (("postgres", 102), ("dlt", 192), ("minio", 282), ("iceberg", 480),
                   ("feast", 692), ("redis", 782)):
        logo(key, 4, y)
    for a, b in ((src, ext), (ext, stg), (stg, bronze), (bronze, silver), (silver, gold),
                 (gold, feast), (feast, redis)):
        down(a, b, 230)

    # --- B. Training & registry (logo bên phải, box x=520..780, tâm 650)
    af = box(520, 105, 260, 55, "Airflow · dag_training")
    train = box(520, 200, 260, 70, "train_agf: AGF-GRU4Rec\n+ Popularity baseline")
    track = box(520, 310, 260, 60, "MLflow Tracking\nparams · metrics · artifacts")
    reg = box(520, 410, 260, 60, "MLflow Model Registry\nreco-mlops-agf")
    promo = box(520, 510, 260, 60, "promote.py\nalias → @production")
    sim = box(520, 610, 260, 60, "similar_items job\ncosine on item table H")
    for key, y in (("airflow", 100), ("pytorch", 202), ("mlflow", 307)):
        logo(key, 788, y)
    down(af, train, 650, color=OPS, dashed=True)
    for a, b in ((train, track), (track, reg), (reg, promo), (promo, sim)):
        down(a, b, 650)
    arrow([(360, 605), (425, 605), (425, 235), (520, 235)], gold, train)
    text(372, 582, "features", 14)
    arrow([(520, 640), (400, 640), (400, 815), (360, 815)], sim, redis)

    # --- C. Serving & deployment (logo bên phải, box x=940..1200, tâm 1070)
    gh = box(940, 105, 260, 50, "GitHub repo")
    jk = box(940, 185, 260, 65, "Jenkins pipeline\nlint · test · model smoke test")
    dk = box(940, 280, 260, 60, "Build & push images\ntag = git SHA")
    dc = box(940, 370, 260, 55, "Docker Compose stack")
    api = box(940, 465, 260, 75, "FastAPI serving\nloads @production\nfrom MLflow")
    ep = box(940, 580, 260, 70, "GET /recommend/homepage\nGET /recommend/similar\nPOST /interact")
    ui = box(940, 700, 260, 60, "Streamlit UI\n(“Đã mua” button)")
    for key, y in (("github", 97), ("jenkins", 185), ("docker", 277), ("fastapi", 470),
                   ("streamlit", 697)):
        logo(key, 1208, y)
    for a, b in ((gh, jk), (jk, dk), (dk, dc), (dc, api), (api, ep)):
        down(a, b, 1070)
    down(ep, ui, 1070, both=True)
    arrow([(780, 540), (900, 540), (900, 495), (940, 495)], promo, api)
    text(788, 517, "load @production", 14)
    arrow([(230, 845), (230, 905), (915, 905), (915, 525), (940, 525)], redis, api)
    text(470, 882, "features + recent_items", 14)

    # --- D. Monitoring & feedback (logo bên phải, box x=1380..1640, tâm 1510)
    am = box(1380, 105, 260, 55, "Airflow · dag_monitoring")
    ed = box(1380, 190, 260, 60, "Evidently data drift\nGold vs reference")
    eq = box(1380, 280, 260, 60, "Evidently model quality\n@production on new data")
    rp = box(1380, 370, 260, 55, "Reports HTML/JSON → MinIO")
    dm = box(1420, 455, 180, 90, "Drift?", shape="diamond")
    yes = box(1340, 580, 165, 60, "Re-run dag_training\n(manual today)", size=15)
    no = box(1520, 580, 165, 60, "Keep @production", size=15)
    prom = box(1380, 680, 260, 55, "Prometheus\nscrapes /metrics")
    graf = box(1380, 765, 260, 55, "Grafana dashboards")
    fb = box(1380, 860, 260, 75, "Feedback loop\n/interact → Redis + source-db\n→ next extract run",
             color=FEEDBACK)
    for key, y in (("airflow", 100), ("evidently", 187), ("minio", 365), ("prometheus", 675),
                   ("grafana", 760)):
        logo(key, 1648, y)
    down(am, ed, 1510, color=OPS, dashed=True)
    for a, b in ((ed, eq), (eq, rp), (rp, dm), (prom, graf)):
        down(a, b, 1510)
    arrow([(1420, 500), (1400, 500), (1400, 580)], dm, yes)
    text(1372, 478, "Yes", 14)
    arrow([(1600, 500), (1620, 500), (1620, 580)], dm, no)
    text(1626, 478, "No", 14)

    # Liên kết giữa C và D
    arrow([(1200, 600), (1330, 600), (1330, 707), (1380, 707)], ep, prom, color=OPS, dashed=True)
    text(1208, 578, "/metrics", 14, OPS)
    arrow([(1200, 635), (1310, 635), (1310, 897), (1380, 897)], ep, fb, color=FEEDBACK)
    text(1208, 642, "POST /interact", 14, FEEDBACK)
    # Vòng phản hồi về source-db (đi theo hành lang phía trên)
    arrow([(1640, 897), (1750, 897), (1750, 42), (230, 42), (230, 105)], fb, src,
          color=FEEDBACK, dashed=True)
    text(1300, 46, "feedback loop → next extract-job run", 14, FEEDBACK)

    # Chú thích
    arrow([(0, 1022), (50, 1022)])
    text(58, 1012, "batch / model flow", 14)
    arrow([(230, 1022), (280, 1022)], color=OPS, dashed=True)
    text(288, 1012, "orchestration / metrics", 14, OPS)
    arrow([(500, 1022), (550, 1022)], color=FEEDBACK)
    text(558, 1012, "online interaction & feedback", 14, FEEDBACK)


def widen(at: float = WIDEN_AT, by: float = WIDEN_BY) -> None:
    """Dời mọi thứ nằm bên phải hoành độ `at` sang phải `by` px."""
    for el in elements:
        if el["type"] == "arrow":
            pts = [(el["x"] + px, el["y"] + py) for px, py in el["points"]]
            pts = [(px + by if px >= at else px, py) for px, py in pts]
            x0, y0 = pts[0]
            el["x"], el["y"] = x0, y0
            el["points"] = [[px - x0, py - y0] for px, py in pts]
            xs = [p[0] for p in el["points"]]
            el["width"] = max(xs) - min(xs)
        elif el["x"] >= at:
            el["x"] += by
        elif el["x"] + el["width"] > at:  # phần tử vắt qua (khung ngoài, cột B, tiêu đề)
            if el["type"] == "text":
                el["x"] += by / 2
            else:
                el["width"] += by


if __name__ == "__main__":
    build()
    widen()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
           "elements": elements, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
           "files": files}
    OUT.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"Đã ghi {OUT} ({len(elements)} phần tử, {len(files)} logo)")
    if missing:
        print("Logo còn thiếu (thả PNG vào docs/diagrams/icons/<tên>.png rồi chạy lại):",
              ", ".join(sorted(set(missing))))
