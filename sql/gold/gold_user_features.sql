-- Per-user recent-item sequence, chronological order (oldest -> newest),
-- capped at MAX_SEQUENCE_LENGTH (10, see
-- libs/src/reco_mlops_libs/iceberg/schema_contracts/gold_user_features.py).
-- Primary input for the GRU sequence model (ADR 0004) — replaces the
-- flat rolling-window aggregates a LightGBM ranker would have needed.
WITH ranked AS (
    SELECT
        user_id,
        product_id,
        event_time,
        ROW_NUMBER() OVER (
            PARTITION BY user_id ORDER BY event_time DESC, interaction_id DESC
        ) AS recency_rank
    FROM silver_interactions
),
recent AS (
    SELECT user_id, product_id, event_time
    FROM ranked
    WHERE recency_rank <= 10
)
SELECT
    user_id,
    array_agg(product_id ORDER BY event_time ASC) AS item_sequence,
    array_agg(event_time ORDER BY event_time ASC) AS event_time_sequence,
    count(*) AS sequence_length,
    max(event_time) AS last_event_time,
    CURRENT_TIMESTAMP AS computed_at
FROM recent
GROUP BY user_id;
