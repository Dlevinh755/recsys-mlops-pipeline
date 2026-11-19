"""Vẽ sơ đồ kiến trúc MLOPS4REC bằng thư viện `diagrams` (mingrammer).

Cài đặt (Windows):
    winget install graphviz
    pip install diagrams

Chạy từ thư mục gốc repo:
    python docs/diagrams/architecture.py

Kết quả (PNG + SVG) trong docs/diagrams/out/, mỗi phần một sơ đồ, đọc trái → phải:
    01_tong_quan        — bức tranh tổng thể, mỗi tầng 1 khối
    02_data_pipeline    — source-db → extract → Bronze → Silver → Gold
    03_feature_training — Gold → Feast/Redis và Gold → train → MLflow → serving
    04_online_serving   — luồng gợi ý trang chủ và POST /interact
    05_van_hanh         — Airflow, giám sát, CI/CD

Logo không có sẵn trong `diagrams` (MinIO, Iceberg, Lakekeeper, Feast, dlt,
Evidently, Streamlit, PyTorch): thả PNG vào docs/diagrams/icons/ đúng tên trong
ICONS (vd. icons/minio.png). Thiếu file nào thì node đó dùng icon chung.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path


def _ensure_graphviz_on_path() -> None:
    """Windows: winget/installer thường không tự thêm Graphviz vào PATH."""
    if shutil.which("dot"):
        return
    candidates = [Path(os.environ.get(v, "")) / "Graphviz" / "bin"
                  for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    candidates += [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Graphviz" / "bin"]
    for c in candidates:
        if (c / "dot.exe").exists():
            os.environ["PATH"] = str(c) + os.pathsep + os.environ["PATH"]
            return
    sys.exit("Không tìm thấy Graphviz (lệnh `dot`). Cài bằng `winget install graphviz`, "
             "mở lại terminal rồi chạy lại; hoặc thêm thư mục ...\\Graphviz\\bin vào PATH.")


_ensure_graphviz_on_path()

from diagrams import Cluster, Diagram, Edge  # noqa: E402
from diagrams.custom import Custom  # noqa: E402

HERE = Path(__file__).resolve().parent
ICON_DIR = HERE / "icons"
OUT_DIR = HERE / "out"

ICONS = {
    "minio": "minio.png", "iceberg": "iceberg.png", "lakekeeper": "lakekeeper.png",
    "feast": "feast.png", "dlt": "dlt.png", "evidently": "evidently.png",
    "streamlit": "streamlit.png", "pytorch": "pytorch.png",
}

FONT = "Arial"  # có sẵn trên Windows, đủ dấu tiếng Việt
GRAPH_ATTR = {
    "fontname": FONT, "fontsize": "20", "pad": "0.5",
    "splines": "ortho",       # đường thẳng gấp khúc, không cong
    "nodesep": "0.9", "ranksep": "1.3",
    "forcelabels": "true",    # ortho không hỗ trợ `label` trên cạnh → dùng xlabel
}
NODE_ATTR = {"fontname": FONT, "fontsize": "13"}
EDGE_ATTR = {"fontname": FONT, "fontsize": "11", "penwidth": "1.4"}

C_DATA = "#0c8599"     # luồng dữ liệu batch
C_MODEL = "#6741d9"    # luồng model
C_ONLINE = "#e8590c"   # online / ngoại lệ /interact
C_OPS = "#868e96"      # điều phối, giám sát

BG = {"data": "#e6fcf5", "model": "#f3f0ff", "online": "#fff4e6", "ops": "#f1f3f5"}

STORAGE = "diagrams.generic.storage:Storage"
DB = "diagrams.generic.database:SQL"
PY = "diagrams.programming.language:Python"
FASTAPI = ["diagrams.programming.framework:Fastapi", "diagrams.programming.framework:FastAPI"]


# ---------------------------------------------------------------- helpers
def _load(path: str):
    module, cls = path.split(":")
    try:
        return getattr(importlib.import_module(module), cls)
    except (ImportError, AttributeError):
        return None


def node(label: str, builtin: list[str] | None = None, icon: str | None = None,
         fallback: str = PY):
    """Ưu tiên PNG tự thêm → node có sẵn trong diagrams → icon chung."""
    if icon:
        png = ICON_DIR / ICONS[icon]
        if png.exists():
            return Custom(label, str(png))
    for path in builtin or []:
        cls = _load(path)
        if cls is not None:
            return cls(label)
    return _load(fallback)(label)


def e(color: str, label: str = "", style: str = "solid", **attrs) -> Edge:
    return Edge(color=color, style=style, xlabel=label, fontcolor=color, **attrs)


def cluster(title: str, kind: str):
    return Cluster(title, graph_attr={"fontname": FONT, "fontsize": "15",
                                      "bgcolor": BG[kind], "margin": "18"})


@contextmanager
def diagram(filename: str, title: str):
    with Diagram(title, filename=str(OUT_DIR / filename), outformat=["png", "svg"],
                 show=False, direction="LR", graph_attr=GRAPH_ATTR,
                 node_attr=NODE_ATTR, edge_attr=EDGE_ATTR) as d:
        yield d


# ------------------------------------------------------------- 1. tổng quan
def tong_quan() -> None:
    with diagram("01_tong_quan", "1. Tổng quan kiến trúc MLOPS4REC"):
        with cluster("Nguồn & ingest", "data"):
            src = node("source-db\n(Postgres)", ["diagrams.onprem.database:PostgreSQL"], fallback=DB)
            ext = node("extract-job\n(dlt)", icon="dlt")
        with cluster("Lakehouse", "data"):
            lake = node("Iceberg\nBronze/Silver/Gold", icon="iceberg", fallback=STORAGE)
        with cluster("Feature store", "data"):
            redis = node("Feast → Redis", ["diagrams.onprem.inmemory:Redis"], fallback=DB)
        with cluster("Training & registry", "model"):
            train = node("training-job\nAGF-GRU4Rec", icon="pytorch")
            mlflow = node("MLflow\nregistry", ["diagrams.onprem.mlops:Mlflow"], fallback=DB)
        with cluster("Serving", "online"):
            api = node("serving\n(FastAPI)", FASTAPI)
            ui = node("ui\n(Streamlit)", icon="streamlit")

        src >> e(C_DATA) >> ext >> e(C_DATA) >> lake
        lake >> e(C_DATA) >> redis >> e(C_DATA) >> api
        lake >> e(C_MODEL) >> train >> e(C_MODEL) >> mlflow >> e(C_MODEL) >> api
        api >> e(C_ONLINE) >> ui
        api >> e(C_ONLINE, "POST /interact", "dashed", constraint="false") >> src


# ------------------------------------------------------- 2. data pipeline
def data_pipeline() -> None:
    with diagram("02_data_pipeline", "2. Data pipeline: ingest → lakehouse"):
        src = node("source-db\nusers / products /\ninteractions",
                   ["diagrams.onprem.database:PostgreSQL"], fallback=DB)
        with cluster("Phase 1 — extract-job", "data"):
            ext = node("dlt incremental\n(watermark)", icon="dlt")
            staging = node("MinIO\nextract-staging\n(Parquet)", icon="minio", fallback=STORAGE)
        with cluster("Phase 2 — transform-job (Iceberg trên MinIO)", "data"):
            bronze = node("Bronze\nappend", icon="iceberg", fallback=STORAGE)
            silver = node("Silver\ndedup + pandera", icon="iceberg", fallback=STORAGE)
            gold = node("Gold\nuser_features /\nitem_features", icon="iceberg", fallback=STORAGE)
        catalog = node("Lakekeeper\nREST catalog", icon="lakekeeper", fallback=DB)

        src >> e(C_DATA) >> ext >> e(C_DATA) >> staging >> e(C_DATA) >> bronze
        bronze >> e(C_DATA) >> silver >> e(C_DATA) >> gold
        catalog >> e(C_OPS, "metadata bảng", "dotted") >> silver


# ---------------------------------------------------- 3. feature & training
def feature_training() -> None:
    with diagram("03_feature_training", "3. Feature store, training và model registry"):
        gold = node("Gold\nuser/item features", icon="iceberg", fallback=STORAGE)
        with cluster("Phase 3 — Feature store", "data"):
            feast = node("materialize-job\n(Feast)", icon="feast")
            redis = node("Redis\nonline store", ["diagrams.onprem.inmemory:Redis"], fallback=DB)
        with cluster("Phase 4/10 — Training & registry", "model"):
            train = node("train_agf\nAGF-GRU4Rec\n+ baseline Pop", icon="pytorch")
            mlflow = node("MLflow\nreco-mlops-agf", ["diagrams.onprem.mlops:Mlflow"], fallback=DB)
            similar = node("similar_items\n(cosine trên H)", icon="pytorch")
        api = node("serving\n(FastAPI)", FASTAPI)

        gold >> e(C_DATA) >> feast >> e(C_DATA) >> redis
        gold >> e(C_MODEL) >> train >> e(C_MODEL, "log + register") >> mlflow
        mlflow >> e(C_MODEL, "promote → production") >> similar
        similar >> e(C_DATA, "similar_items:{id}") >> redis
        redis >> e(C_DATA) >> api
        mlflow >> e(C_MODEL, "poll alias, bảng H") >> api


# ------------------------------------------------------- 4. online serving
def online_serving() -> None:
    with diagram("04_online_serving", "4. Luồng online: gợi ý trang chủ và \"Đã mua\""):
        user = node("Người dùng", ["diagrams.onprem.client:User"])
        ui = node("ui\n(Streamlit)", icon="streamlit")
        api = node("serving\n(FastAPI)", FASTAPI)
        with cluster("Redis", "data"):
            cache = node("cache:homepage", ["diagrams.onprem.inmemory:Redis"], fallback=DB)
            recent = node("recent_items /\nsimilar_items", ["diagrams.onprem.inmemory:Redis"], fallback=DB)
        with cluster("Model production", "model"):
            model = node("AGF-GRU4Rec\nH[ứng viên] · s_u", icon="pytorch")
        src = node("source-db\ninteractions", ["diagrams.onprem.database:PostgreSQL"], fallback=DB)

        user >> e(C_MODEL) >> ui
        ui >> e(C_MODEL, "1. GET /recommend/homepage") >> api
        api >> e(C_MODEL, "2. tra cache") >> cache
        api >> e(C_MODEL, "3. chuỗi + ứng viên") >> recent
        api >> e(C_MODEL, "4. chấm điểm → top-10") >> model
        ui >> e(C_ONLINE, "A. POST /interact", "dashed") >> api
        api >> e(C_ONLINE, "B. ghi recent_items", "dashed") >> recent
        api >> e(C_ONLINE, "C. ghi interaction", "dashed") >> src
        api >> e(C_ONLINE, "D. xoá cache", "dashed") >> cache


# ------------------------------------------------------------ 5. vận hành
def van_hanh() -> None:
    with diagram("05_van_hanh", "5. Vận hành: điều phối, giám sát, CI/CD"):
        with cluster("Phase 6 — Điều phối (JobDockerOperator)", "ops"):
            airflow = node("Airflow", ["diagrams.onprem.workflow:Airflow"])
            jobs = [node("extract-job", icon="dlt"),
                    node("transform-job", icon="iceberg"),
                    node("materialize-job", icon="feast"),
                    node("training-job", icon="pytorch"),
                    node("monitoring-job", icon="evidently")]
            airflow >> e(C_OPS) >> jobs

        with cluster("Phase 7 — Giám sát", "ops"):
            api = node("serving\n/metrics", FASTAPI)
            prom = node("Prometheus", ["diagrams.onprem.monitoring:Prometheus"])
            graf = node("Grafana", ["diagrams.onprem.monitoring:Grafana"])
            gold = node("Gold +\nmodel production", icon="iceberg", fallback=STORAGE)
            evid = node("Evidently\ndrift / quality", icon="evidently")
            report = node("MinIO\nreport HTML/JSON", icon="minio", fallback=STORAGE)
            api >> e(C_OPS) >> prom >> e(C_OPS) >> graf
            gold >> e(C_OPS) >> evid >> e(C_OPS) >> report

        with cluster("Phase 8 — CI/CD", "ops"):
            git = node("GitHub", ["diagrams.onprem.vcs:Github"])
            jenkins = node("Jenkins\nlint · test ·\nmodel smoke test", ["diagrams.onprem.ci:Jenkins"])
            registry = node("Docker Hub\nimage tag git SHA", ["diagrams.onprem.container:Docker"])
            git >> e(C_OPS, "poll SCM") >> jenkins >> e(C_OPS, "build & push") >> registry


if __name__ == "__main__":
    ICON_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    for draw in (tong_quan, data_pipeline, feature_training, online_serving, van_hanh):
        draw()
    print(f"Đã ghi 5 sơ đồ vào {OUT_DIR}")
