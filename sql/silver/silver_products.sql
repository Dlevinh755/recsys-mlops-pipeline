-- Dedupe bronze.products down to one row per product_id, keeping the
-- version with the latest updated_at (ties broken by the most recently
-- ingested Bronze row). Plain SQL, no DuckDB-specific syntax, so it can
-- run unchanged on another engine later (see docs/bao-cao-ky-thuat.md 3.1).
WITH ranked AS (
    SELECT
        product_id,
        title,
        category,
        brand,
        price,
        description,
        image_url,
        is_active,
        created_at,
        updated_at,
        ROW_NUMBER() OVER (
            PARTITION BY product_id
            ORDER BY updated_at DESC, _ingested_at DESC
        ) AS row_rank
    FROM bronze_products
)
SELECT
    product_id,
    title,
    category,
    brand,
    price,
    description,
    image_url,
    is_active,
    created_at,
    updated_at
FROM ranked
WHERE row_rank = 1;
