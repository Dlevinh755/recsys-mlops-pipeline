BEGIN;

CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT,
    price NUMERIC(12, 2) CHECK (price IS NULL OR price >= 0),
    description TEXT,
    image_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE interactions (
    interaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    event_type TEXT NOT NULL CHECK (event_type IN ('view', 'cart', 'purchase', 'rating')),
    rating NUMERIC(2, 1) CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    event_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((event_type = 'rating' AND rating IS NOT NULL) OR (event_type <> 'rating' AND rating IS NULL))
);

CREATE INDEX idx_products_watermark
    ON products (updated_at, product_id);

CREATE INDEX idx_interactions_watermark
    ON interactions (updated_at, interaction_id);

CREATE INDEX idx_interactions_user_event_time
    ON interactions (user_id, event_time DESC);

-- No `pipeline` schema / watermark bookkeeping table here anymore: Phase 1
-- extract now uses dlt's own incremental cursor + pipeline state (restored
-- from MinIO, not Postgres) — see
-- docs/decisions/0006-dlt-native-incremental-extract.md.

COMMENT ON TABLE interactions IS
    'Append-friendly source events. Phase 1 reads by the total-order cursor (updated_at, interaction_id).';

COMMIT;
