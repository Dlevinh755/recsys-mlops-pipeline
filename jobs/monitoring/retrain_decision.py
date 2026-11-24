"""ADR 0014 — decide whether to retrain, and say why.

The DAG (`dag_continuous_training`) only wires jobs; the logic lives here
(CLAUDE.md nguyên tắc #1). Result: JSON on stdout, a copy on MinIO
(`monitoring-reports/retrain_decision/<ts>.json`, the evidence), and the exit
code — **0 = retrain, 99 = skip** (the DAG maps 99 to "skipped"; nothing flows
through XCom).

Signals (all read from durable state, none passed in):

* the latest `data_drift` report on MinIO (`reco_summary`: `dataset_drift`,
  `current_rows` = Silver rows now);
* MLflow: when a version was last trained (any version, promoted or not), the
  production version's age, and how many Silver rows the latest trained version
  saw (`silver_rows` param, logged by `train_agf`).

`new_rows` is counted against the **latest trained version**, not against the
drift baseline (which follows the production model): a challenger that was
rejected has already consumed those rows, so it must not be retrained on the
same data again every cooldown. Versions trained before `silver_rows` existed
fall back to the drift baseline's row count.

Policy (env, defaults in `.env.example`): `RETRAIN_MIN_NEW_ROWS`,
`RETRAIN_NEW_ROWS_RATIO`, `RETRAIN_MAX_MODEL_AGE_DAYS`, `RETRAIN_COOLDOWN_HOURS`.
With only two compared columns, one drifting column already reads as
`dataset_drift` (share 0.5), hence the row floor next to the drift rule.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field

from jobs.monitoring.baseline import baseline_filesystem
from jobs.monitoring.report_sink import save_report
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.mlflow_utils.registry_client import get_client, get_production_version
from reco_mlops_libs.ranking.base import REGISTERED_MODEL_NAME

EXIT_RETRAIN = 0
EXIT_SKIP = 99
SILVER_ROWS_PARAM = "silver_rows"


@dataclass(frozen=True)
class Policy:
    min_new_rows: int = 200
    new_rows_ratio: float = 0.20
    max_model_age_days: float = 14.0
    cooldown_hours: float = 24.0

    @classmethod
    def from_env(cls) -> "Policy":
        d = cls()
        return cls(
            min_new_rows=int(os.getenv("RETRAIN_MIN_NEW_ROWS", d.min_new_rows)),
            new_rows_ratio=float(os.getenv("RETRAIN_NEW_ROWS_RATIO", d.new_rows_ratio)),
            max_model_age_days=float(os.getenv("RETRAIN_MAX_MODEL_AGE_DAYS", d.max_model_age_days)),
            cooldown_hours=float(os.getenv("RETRAIN_COOLDOWN_HOURS", d.cooldown_hours)),
        )


@dataclass(frozen=True)
class Signals:
    dataset_drift: bool | None  # None = no drift report found
    drifted_columns: list[str]
    current_rows: int  # Silver rows now
    reference_rows: int  # Silver rows the latest trained version saw (or fallback)
    reference_source: str  # "latest_version" | "drift_baseline"
    hours_since_last_train: float | None  # None = nothing was ever trained
    production_age_days: float | None  # None = no production version


@dataclass(frozen=True)
class Decision:
    retrain: bool
    reasons: list[str]
    new_rows: int
    new_rows_ratio: float | None
    signals: Signals = field(repr=False)

    def as_dict(self) -> dict:
        return {"retrain": self.retrain, "reasons": self.reasons, "new_rows": self.new_rows,
                "new_rows_ratio": self.new_rows_ratio, "signals": asdict(self.signals)}


def decide(signals: Signals, policy: Policy) -> Decision:
    """Pure (no I/O)."""
    new_rows = max(0, signals.current_rows - signals.reference_rows)
    ratio = new_rows / signals.reference_rows if signals.reference_rows > 0 else None

    def verdict(retrain: bool, reasons: list[str]) -> Decision:
        return Decision(retrain, reasons, new_rows, ratio, signals)

    if signals.hours_since_last_train is not None and signals.hours_since_last_train < policy.cooldown_hours:
        return verdict(False, [f"cooldown: last train {signals.hours_since_last_train:.1f}h ago "
                               f"< {policy.cooldown_hours:g}h"])

    reasons: list[str] = []
    notes: list[str] = []

    if signals.production_age_days is None:
        reasons.append("no production model yet")
    elif signals.production_age_days >= policy.max_model_age_days:
        reasons.append(f"model age {signals.production_age_days:.1f}d >= {policy.max_model_age_days:g}d")
    else:
        notes.append(f"model age {signals.production_age_days:.1f}d < {policy.max_model_age_days:g}d")

    if signals.dataset_drift is None:
        notes.append("no drift report found")
    elif signals.dataset_drift and new_rows >= policy.min_new_rows:
        reasons.append(f"dataset drift ({', '.join(signals.drifted_columns) or 'no column named'}) "
                       f"with {new_rows} new rows >= {policy.min_new_rows}")
    elif signals.dataset_drift:
        notes.append(f"dataset drift but only {new_rows} new rows < {policy.min_new_rows}")
    else:
        notes.append("no dataset drift")

    if ratio is not None and new_rows > 0 and ratio >= policy.new_rows_ratio:
        reasons.append(f"new rows {new_rows}/{signals.reference_rows} = {ratio:.1%} >= {policy.new_rows_ratio:.0%}")
    elif ratio is None and new_rows > 0:
        reasons.append(f"{new_rows} new rows and no reference row count")
    else:
        notes.append(f"new rows {new_rows}"
                     + (f"/{signals.reference_rows} = {ratio:.1%} < {policy.new_rows_ratio:.0%}" if ratio is not None else ""))

    return verdict(bool(reasons), reasons or [f"nothing to do ({'; '.join(notes)})"])


# ------------------------------------------------------------------ I/O


def latest_drift_summary(fs, bucket: str, job_name: str = "data_drift") -> dict | None:
    """`reco_summary` of the newest `<job_name>/*.json`, `None` if none exists."""
    paths = sorted(fs.glob(f"{bucket}/{job_name}/*.json"))  # timestamped names sort by time
    if not paths:
        return None
    with fs.open(paths[-1], "r") as file:
        report = json.load(file)
    summary = report.get("reco_summary")
    if summary is not None:
        summary = {**summary, "report_path": paths[-1]}
    return summary


def collect_signals(client, fs, bucket: str, now: float | None = None) -> Signals:
    now = time.time() if now is None else now
    drift = latest_drift_summary(fs, bucket)
    if drift is None:
        raise RuntimeError("no data_drift report with reco_summary on MinIO — run data_drift_report first")

    versions = client.search_model_versions(f"name='{REGISTERED_MODEL_NAME}'")
    latest = max(versions, key=lambda v: int(v.version)) if versions else None
    hours = (now - latest.creation_timestamp / 1000) / 3600 if latest else None

    reference_rows, source = int(drift["baseline_rows"]), "drift_baseline"
    if latest is not None:
        trained_rows = client.get_run(latest.run_id).data.params.get(SILVER_ROWS_PARAM)
        if trained_rows is not None:
            reference_rows, source = int(trained_rows), "latest_version"

    production = get_production_version(REGISTERED_MODEL_NAME, client=client)
    age = (now - production.creation_timestamp / 1000) / 86400 if production else None
    return Signals(
        dataset_drift=drift["dataset_drift"],
        drifted_columns=list(drift.get("drifted_columns", [])),
        current_rows=int(drift["current_rows"]),
        reference_rows=reference_rows,
        reference_source=source,
        hours_since_last_train=hours,
        production_age_days=age,
    )


def main() -> int:
    policy = Policy.from_env()
    fs = baseline_filesystem()
    signals = collect_signals(get_client(), fs, require_env("MONITORING_REPORTS_BUCKET"))
    decision = decide(signals, policy)

    payload = {**decision.as_dict(), "policy": asdict(policy)}
    payload["report_paths"] = save_report("retrain_decision", data=payload)
    print(json.dumps({"retrain_decision": payload}))
    return EXIT_RETRAIN if decision.retrain else EXIT_SKIP


if __name__ == "__main__":
    sys.exit(main())
