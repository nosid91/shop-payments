-- Demo data for local development. Safe to run repeatedly.

INSERT INTO users (id, email, name) VALUES
    ('11111111-1111-1111-1111-111111111111', 'alice@example.com', 'Alice'),
    ('22222222-2222-2222-2222-222222222222', 'bob@example.com',   'Bob')
ON CONFLICT DO NOTHING;

INSERT INTO products (id, name, price, currency, stock_quantity) VALUES
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Blue Kettle',   45.00, 'USD',  10),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'Wool Blanket',  89.99, 'USD',   5),
    ('cccccccc-cccc-cccc-cccc-cccccccccccc', 'Ceramic Mug',   12.50, 'USD', 100)
ON CONFLICT DO NOTHING;

INSERT INTO carts (id, user_id, status) VALUES
    ('c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1',
     '11111111-1111-1111-1111-111111111111',
     'active'),
    ('c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2',
     '22222222-2222-2222-2222-222222222222',
     'active')
ON CONFLICT DO NOTHING;

INSERT INTO cart_items (id, cart_id, product_id, quantity, unit_price) VALUES
    ('d1d1d1d1-d1d1-d1d1-d1d1-d1d1d1d1d1d1',
     'c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1',
     'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 1, 45.00),
    ('d2d2d2d2-d2d2-d2d2-d2d2-d2d2d2d2d2d2',
     'c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1',
     'cccccccc-cccc-cccc-cccc-cccccccccccc', 2, 12.50),
    ('d3d3d3d3-d3d3-d3d3-d3d3-d3d3d3d3d3d3',
     'c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2',
     'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 1, 89.99)
ON CONFLICT DO NOTHING;

INSERT INTO user_payment_methods (id, user_id, provider_token, last_four, is_default) VALUES
    ('11111111-2222-3333-4444-555555555555',
     '11111111-1111-1111-1111-111111111111',
     'tok_test_alice_visa', '4242', TRUE),
    ('22222222-2222-3333-4444-555555555551',
     '22222222-2222-2222-2222-222222222222',
     'tok_card_declined', '0002', FALSE),
    ('22222222-2222-3333-4444-555555555552',
     '22222222-2222-2222-2222-222222222222',
     'tok_provider_unavailable', '0003', FALSE),
    ('22222222-2222-3333-4444-555555555553',
     '22222222-2222-2222-2222-222222222222',
     'tok_provider_timeout', '0004', FALSE)
ON CONFLICT DO NOTHING;
