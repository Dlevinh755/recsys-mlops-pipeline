"""MLflow Model Registry access — one connection point used by both
`jobs/training/promote.py` (Phase 4) and Phase 5's `model_loader.py`, same
"single configured client" pattern as `iceberg/catalog.py::get_catalog()`.

Uses MLflow's **alias** mechanism (`set_registered_model_alias` /
`get_model_version_by_alias`) to mark the version serving should use, not
the older stage-based API (Staging/Production/Archived). Stages are
deprecated in current MLflow versions (the tracking server here pins
`v3.12.0`) — aliases are the supported replacement. `de-xuat-trien-khai.md`/
`bao-cao-ky-thuat.md` talk about a version being "in production status";
the `production` alias is how that requirement is satisfied with the
current API. See `docs/modules/phase-4-training.md` for the full note.
"""

from __future__ import annotations

from mlflow import MlflowClient
from mlflow.entities.model_registry import ModelVersion
from mlflow.exceptions import MlflowException

from reco_mlops_libs.common.env import require_env

PRODUCTION_ALIAS = "production"


def get_client() -> MlflowClient:
    return MlflowClient(tracking_uri=require_env("MLFLOW_TRACKING_URI"))


def get_production_version(
    name: str, client: MlflowClient | None = None
) -> ModelVersion | None:
    """Return the registered model version currently aliased `production`,
    or `None` if the model or the alias doesn't exist yet (first-ever
    training run for `name`)."""
    client = client or get_client()
    try:
        return client.get_model_version_by_alias(name, PRODUCTION_ALIAS)
    except MlflowException:
        return None


def set_production_version(
    name: str, version: str, client: MlflowClient | None = None
) -> None:
    """Point the `production` alias of registered model `name` at `version`.

    Moves the alias if it already points elsewhere — MLflow aliases are
    single-pointer per name, so this is how promotion/rollback both work.
    """
    client = client or get_client()
    client.set_registered_model_alias(name, PRODUCTION_ALIAS, version)
