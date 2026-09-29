-- Per-item aggregate features from silver.products + silver.interactions.
-- Recomputed fully every run (MVP scale, see
-- docs/modules/phase-2-lakehouse.md). Per-user features are Phase 3 scope
-- (jobs/features/build_user_features.py).
SELECT
    p.product_id AS product_id,
    p.title AS title,
    p.category AS category,
    p.brand AS brand,
    p.price AS price,
    p.image_url AS image_url,
    COUNT(i.interaction_id) AS num_interactions,
    COUNT(i.interaction_id) FILTER (WHERE i.event_type = 'view') AS num_views,
    COUNT(i.interaction_id) FILTER (WHERE i.event_type = 'cart') AS num_cart_adds,
    COUNT(i.interaction_id) FILTER (WHERE i.event_type = 'purchase') AS num_purchases,
    COUNT(i.interaction_id) FILTER (WHERE i.event_type = 'rating') AS num_ratings,
    AVG(i.rating) FILTER (WHERE i.event_type = 'rating') AS avg_rating,
    COUNT(DISTINCT i.user_id) AS distinct_users,
    MAX(i.event_time) AS last_interaction_at,
    CURRENT_TIMESTAMP AS computed_at
FROM silver_products AS p
LEFT JOIN silver_interactions AS i ON i.product_id = p.product_id
GROUP BY p.product_id, p.title, p.category, p.brand, p.price, p.image_url;
