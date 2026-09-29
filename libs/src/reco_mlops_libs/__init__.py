"""Shared utilities for the Recommendation MLOps project."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("reco-mlops-libs")
except PackageNotFoundError:
    __version__ = "0.0.1"

__all__ = ["__version__"]
