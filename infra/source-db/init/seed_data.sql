BEGIN;

INSERT INTO users (user_id, display_name, created_at, updated_at) VALUES
    ('u001', 'An',  '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
    ('u002', 'Binh','2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
    ('u003', 'Chi', '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00');

INSERT INTO products (
    product_id, title, category, brand, price, description, created_at, updated_at
) VALUES
    ('p001', 'Wireless Mouse', 'electronics', 'DemoTech', 19.90, 'Compact wireless mouse', '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
    ('p002', 'Mechanical Keyboard', 'electronics', 'DemoTech', 59.90, 'Tenkeyless keyboard', '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
    ('p003', 'Data Engineering Book', 'books', 'DemoPress', 29.50, 'Introductory data engineering book', '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
    ('p004', 'Insulated Bottle', 'home', 'DemoHome', 14.00, 'Reusable insulated bottle', '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00'),
    ('p005', 'USB-C Hub', 'electronics', 'DemoTech', 34.90, 'Multi-port USB-C hub', '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00');

INSERT INTO interactions (user_id, product_id, event_type, rating, event_time, created_at, updated_at) VALUES
    ('u001', 'p001', 'view',     NULL, '2026-01-02 08:00:00+00', '2026-01-02 08:00:00+00', '2026-01-02 08:00:00+00'),
    ('u001', 'p001', 'purchase', NULL, '2026-01-02 08:05:00+00', '2026-01-02 08:05:00+00', '2026-01-02 08:05:00+00'),
    ('u001', 'p002', 'view',     NULL, '2026-01-03 09:00:00+00', '2026-01-03 09:00:00+00', '2026-01-03 09:00:00+00'),
    ('u002', 'p003', 'view',     NULL, '2026-01-02 10:00:00+00', '2026-01-02 10:00:00+00', '2026-01-02 10:00:00+00'),
    ('u002', 'p003', 'rating',    5.0, '2026-01-02 10:30:00+00', '2026-01-02 10:30:00+00', '2026-01-02 10:30:00+00'),
    ('u002', 'p004', 'cart',     NULL, '2026-01-04 11:00:00+00', '2026-01-04 11:00:00+00', '2026-01-04 11:00:00+00'),
    ('u003', 'p005', 'view',     NULL, '2026-01-05 13:00:00+00', '2026-01-05 13:00:00+00', '2026-01-05 13:00:00+00'),
    ('u003', 'p005', 'purchase', NULL, '2026-01-05 13:10:00+00', '2026-01-05 13:10:00+00', '2026-01-05 13:10:00+00');

COMMIT;
