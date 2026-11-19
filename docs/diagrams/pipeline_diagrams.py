"""Sơ đồ pipeline MLOPS4REC bằng `diagrams` (mingrammer) — bố cục 4 cột
giống bản Excalidraw: Data → Training → Serving → Monitoring.

Cài đặt (Windows):
    winget install graphviz
    pip install diagrams

Chạy từ thư mục gốc repo:
    python docs/diagrams/pipeline_diagrams.py

Kết quả: docs/diagrams/out/mlops4rec_pipeline_diagrams.png / .svg

Logo không có sẵn trong `diagrams` (dlt, Iceberg, Feast, Evidently,
Streamlit, MinIO, PyTorch): thả PNG vào docs/diagrams/icons/<tên>.png.
Thiếu file nào thì bước đó vẽ thành khung chữ thường.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
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

from diagrams import Cluster, Diagram, Edge, Node  # noqa: E402
from diagrams.custom import Custom  # noqa: E402

HERE = Path(__file__).resolve().parent
ICON_DIR = HERE / "icons"
OUT = HERE / "out" / "mlops4rec_pipeline_diagrams"

BUILTIN = {
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

FONT = "Arial"
INK = "#343a40"
OPS = "#868e96"
FEEDBACK = "#e8590c"

GRAPH_ATTR = {
    "fontname": FONT, "fontsize": "22", "labelloc": "t", "pad": "0.5",
    "splines": "ortho", "nodesep": "0.45", "ranksep": "0.45",
    "newrank": "true", "forcelabels": "true",
}
NODE_ATTR = {"fontname": FONT, "fontsize": "12"}
EDGE_ATTR = {"fontname": FONT, "fontsize": "11", "color": INK, "penwidth": "1.3"}

missing: list[str] = []


def _builtin(key: str):
    for path in BUILTIN.get(key, []):
        module, cls = path.split(":")
        try:
            return getattr(importlib.import_module(module), cls)
        except (ImportError, AttributeError):
            continue
    return None


def step(label: str, *, shape: str = "box", color: str = INK, width: str = "2.5",
         height: str = "0.75") -> Node:
    """Bước không có logo: khung bo góc, chữ ở giữa (như hình mẫu)."""
    return Node(label, shape=shape, style="rounded,filled" if shape == "box" else "filled",
                fillcolor="white", color=color, fontcolor=color, fixedsize="true",
                width=width, height=height, labelloc="c", penwidth="1.2")


def logo_step(label: str, key: str) -> Node:
    """Bước có logo: logo ở trên, nhãn bên dưới; thiếu logo → khung chữ."""
    png = ICON_DIR / f"{key}.png"
    if png.exists():
        return Custom(label, str(png))
    cls = _builtin(key)
    if cls is not None:
        return cls(label)
    missing.append(key)
    return step(label)


def e(color: str = INK, label: str = "", style: str = "solid", **attrs) -> Edge:
    return Edge(color=color, style=style, xlabel=label, fontcolor=color, **attrs)


def cross(color: str = INK, label: str = "", style: str = "solid") -> Edge:
    """Cạnh nối giữa 2 cột: không ảnh hưởng thứ hạng để các cột giữ thẳng hàng."""
    return e(color, label, style, constraint="false")


def column(title: str, bg: str) -> Cluster:
    return Cluster(title, graph_attr={"fontname": FONT, "fontsize": "16", "bgcolor": bg,
                                      "pencolor": "#495057", "labeljust": "c", "margin": "16"})


def build() -> None:
    with Diagram("MLOPS4REC — End-to-end MLOps pipeline for sequential recommendation "
                 "(AGF-GRU4Rec)", filename=str(OUT), outformat=["png", "svg"], show=False,
                 direction="TB", graph_attr=GRAPH_ATTR, node_attr=NODE_ATTR,
                 edge_attr=EDGE_ATTR):

        with column("Data_And_Lakehouse", "#f0fbf8"):
            src = logo_step("source-db (Postgres)\nusers · products · interactions", "postgres")
            ext = logo_step("extract-job\ndlt incremental", "dlt")
            stg = logo_step("MinIO extract-staging\nParquet", "minio")
            with Cluster("Iceberg · Lakekeeper catalog",
                         graph_attr={"fontname": FONT, "fontsize": "12", "bgcolor": "#ffffff",
                                     "style": "dashed,rounded", "labeljust": "c"}):
                bronze = step("Bronze — append")
                silver = step("Silver — dedup + pandera")
                gold = step("Gold\nuser_features · item_features")
            feast = logo_step("materialize-job (Feast)", "feast")
            redis = logo_step("Redis online store\nrecent_items · similar_items", "redis")
            src >> e() >> ext >> e() >> stg >> e() >> bronze >> e() >> silver >> e() >> gold
            gold >> e() >> feast >> e() >> redis

        with column("Training_And_Registry", "#f5f2ff"):
            af = logo_step("Airflow · dag_training", "airflow")
            train = logo_step("train_agf: AGF-GRU4Rec\n+ Popularity baseline", "pytorch")
            track = logo_step("MLflow Tracking\nparams · metrics · artifacts", "mlflow")
            reg = step("MLflow Model Registry\nreco-mlops-agf")
            promo = step("promote.py\nalias → @production")
            sim = step("similar_items job\ncosine on item table H")
            af >> e(OPS, style="dashed") >> train
            train >> e() >> track >> e() >> reg >> e() >> promo >> e() >> sim

        with column("Serving_And_Deployment", "#eef7fc"):
            gh = logo_step("GitHub repo", "github")
            jk = logo_step("Jenkins pipeline\nlint · test · model smoke test", "jenkins")
            dk = logo_step("Build & push images\ntag = git SHA", "docker")
            dc = step("Docker Compose stack")
            api = logo_step("FastAPI serving\nloads @production from MLflow", "fastapi")
            ep = step("GET /recommend/homepage\nGET /recommend/similar\nPOST /interact",
                      height="0.95")
            ui = logo_step("Streamlit UI", "streamlit")
            gh >> e() >> jk >> e() >> dk >> e() >> dc >> e() >> api >> e() >> ep
            ep >> e(dir="both") >> ui

        with column("Monitoring_And_Feedback", "#fff6f0"):
            am = logo_step("Airflow · dag_monitoring", "airflow")
            ed = logo_step("Evidently data drift\nGold vs reference", "evidently")
            eq = step("Evidently model quality\n@production on new data")
            rp = step("Reports HTML/JSON → MinIO")
            dm = step("Drift?", shape="diamond", width="1.6", height="1.0")
            yes = step("Re-run dag_training\n(manual today)", width="1.9")
            no = step("Keep @production", width="1.9")
            prom = logo_step("Prometheus\nscrapes /metrics", "prometheus")
            graf = logo_step("Grafana dashboards", "grafana")
            fb = step("Feedback loop\n/interact → Redis + source-db\n→ next extract run",
                      color=FEEDBACK, height="0.95")
            am >> e(OPS, style="dashed") >> ed
            ed >> e() >> eq >> e() >> rp >> e() >> dm
            dm >> e(label="Yes") >> yes
            dm >> e(label="No") >> no
            yes >> Edge(style="invis") >> prom
            prom >> e() >> graf >> Edge(style="invis") >> fb

        # Khoá thứ tự 4 cột trái → phải: nút gốc ẩn với ordering=out, các cạnh
        # ẩn đi ra theo đúng thứ tự cột (không có nó, các cạnh chéo cột bên
        # dưới làm Graphviz đảo chỗ các cột).
        root = Node("", style="invis", shape="point", width="0", height="0",
                    fixedsize="true", ordering="out")
        root >> Edge(style="invis") >> [src, af, gh, am]

        # Liên kết giữa các cột
        gold >> cross(label="features") >> train
        sim >> cross(label="similar_items:{id}") >> redis
        promo >> cross(label="load @production") >> api
        redis >> cross(label="features + recent_items") >> api
        ep >> cross(OPS, "/metrics", "dashed") >> prom
        ep >> cross(FEEDBACK, "POST /interact") >> fb
        fb >> cross(FEEDBACK, "feedback loop", "dashed") >> src


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build()
    print(f"Đã ghi {OUT}.png / .svg")
    if missing:
        print("Logo còn thiếu (thả PNG vào docs/diagrams/icons/<tên>.png rồi chạy lại):",
              ", ".join(sorted(set(missing))))
