from __future__ import annotations

import os


def require_env(name: str) -> str:
    """Return the environment variable or raise if it is unset/empty.

    Used for connection settings (catalog URI, S3 credentials, DSNs) where a
    silently missing value would otherwise fail deep inside a third-party
    client with a confusing error.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return value


def get_env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def get_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default
