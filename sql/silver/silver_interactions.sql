-- Dedupe bronze.interactions down to one row per interaction_id. The
-- natural key never changes source-side, but a row can be re-extracted
-- (e.g. a rating correction bumps updated_at), so keep the latest version.
WITH ranked AS (
    SELECT
        interaction_id,
        user_id,
        product_id,
        event_type,
        rating,
        event_time,
        created_at,
        updated_at,
        ROW_NUMBER() OVER (
            PARTITION BY interaction_id
            ORDER BY updated_at DESC, _ingested_at DESC
        ) AS row_rank
    FROM bronze_interactions
)
SELECT
    interaction_id,
    user_id,
    product_id,
    event_type,
    rating,
    event_time,
    created_at,
    updated_at
FROM ranked
WHERE row_rank = 1;
