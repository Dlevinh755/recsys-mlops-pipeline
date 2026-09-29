"""Phase 5 serving configuration — reads env vars via the same
`reco_mlops_libs.common.env` helpers every other job uses, no new config
mechanism introduced just for this service.
"""

from __future__ import annotations

from dataclasses import dataclass

from reco_mlops_libs.common.env import get_env, get_int_env, require_env


@dataclass(frozen=True)
class Settings:
    redis_host: str
    redis_port: int
    source_db_dsn: str
    mlflow_tracking_uri: str
    model_poll_interval_seconds: int
    fallback_refresh_interval_seconds: int
    homepage_top_n: int
    user_list_size: int
    cache_ttl_seconds: int
    similar_cache_ttl_seconds: int
    recent_items_ttl_seconds: int
    recent_items_max_length: int


def load_settings() -> Settings:
    cache_ttl = get_int_env("CACHE_TTL_SECONDS", 60)
    return Settings(
        redis_host=get_env("REDIS_HOST", "redis"),
        redis_port=get_int_env("REDIS_PORT", 6379),
        source_db_dsn=require_env("SOURCE_DB_DSN"),
        mlflow_tracking_uri=require_env("MLFLOW_TRACKING_URI"),
        model_poll_interval_seconds=get_int_env("MODEL_POLL_INTERVAL_SECONDS", 60),
        fallback_refresh_interval_seconds=get_int_env("FALLBACK_REFRESH_INTERVAL_SECONDS", 300),
        homepage_top_n=get_int_env("HOMEPAGE_TOP_N", 10),
        user_list_size=get_int_env("USER_LIST_SIZE", 50),
        cache_ttl_seconds=cache_ttl,
        # Similar-items results don't depend on user session state, so they
        # don't need invalidating from POST /interact — safe to cache longer.
        similar_cache_ttl_seconds=cache_ttl * 5,
        recent_items_ttl_seconds=get_int_env("RECENT_ITEMS_TTL_SECONDS", 1800),
        recent_items_max_length=get_int_env("RECENT_ITEMS_MAX_LENGTH", 20),
    )
