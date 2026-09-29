#!/usr/bin/env python3
"""Fail fast on an incomplete or syntactically invalid Phase 0 scaffold."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    ".env.example",
    ".gitignore",
    ".dockerignore",
    ".pre-commit-config.yaml",
    "Makefile",
    "README.md",
    "docker-compose.yml",
    "infra/minio/init-buckets.sh",
    "infra/iceberg-catalog/catalog.env",
    "infra/iceberg-catalog/bootstrap.sh",
    "infra/source-db/init/schema.sql",
    "infra/source-db/init/seed_data.sql",
    "infra/docker/mlflow/Dockerfile",
    "libs/pyproject.toml",
)


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    if missing:
        print(f"Missing Phase 0 files: {', '.join(missing)}", file=sys.stderr)
        return 1

    with (ROOT / "libs/pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    # Only the package name + presence of a version are a Phase 0 contract.
    # The version itself is expected to grow past 0.0.1 from Phase 2 onward
    # as libs/ gains real modules (see libs/CHANGELOG.md).
    if project["name"] != "reco-mlops-libs" or not project.get("version"):
        print("Internal package metadata does not match Phase 0 contract", file=sys.stderr)
        return 1

    run(["sh", "-n", "infra/minio/init-buckets.sh"])
    run(["sh", "-n", "infra/iceberg-catalog/bootstrap.sh"])
    run(["sh", "-n", "scripts/smoke_test.sh"])

    for yaml_path in (
        "docker-compose.yml",
        "docker-compose.override.yml",
        ".pre-commit-config.yaml",
    ):
        with (ROOT / yaml_path).open(encoding="utf-8") as file:
            yaml.safe_load(file)

    with (ROOT / "docker-compose.yml").open(encoding="utf-8") as file:
        compose = yaml.safe_load(file)
    expected_services = {
        "source-db",
        "catalog-db",
        "mlflow-db",
        "minio",
        "minio-init",
        "catalog-migrate",
        "lakekeeper",
        "catalog-bootstrap",
        "mlflow",
    }
    missing_services = expected_services.difference(compose.get("services", {}))
    if missing_services:
        print(f"Missing Compose services: {', '.join(sorted(missing_services))}", file=sys.stderr)
        return 1

    docker = shutil.which("docker")
    if docker:
        run([docker, "compose", "--env-file", ".env.example", "config", "--quiet"])
        print("PASS: required files, YAML/shell syntax, package metadata, Docker Compose config")
    else:
        print("PASS: required files, YAML/shell syntax, package metadata")
        print("SKIP: Docker Compose config (docker executable is unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
