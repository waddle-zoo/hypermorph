-- Deterministic minimal revenue fixture data, matching the domain shape
-- documented in tests/fixtures/superset/6.1.0/revenue/ (hy-gh-28 owns the full-fidelity
-- demo dataset; this is just enough real data for #27's connector contract
-- suite to have something genuine to connect Superset to and sync).
--
-- Logically separate from Hyperset's own Postgres (analytics-db is the
-- *source* Superset queries, not Hyperset's system of record).

CREATE TABLE customer_dim (
    customer_id VARCHAR PRIMARY KEY,
    signup_date DATE NOT NULL,
    region VARCHAR NOT NULL,
    segment VARCHAR NOT NULL,
    is_test BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE finance_orders_daily (
    order_id VARCHAR PRIMARY KEY,
    customer_id VARCHAR NOT NULL REFERENCES customer_dim(customer_id),
    order_date DATE NOT NULL,
    status VARCHAR NOT NULL,
    gross_amount NUMERIC(12, 2) NOT NULL,
    tax_amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR NOT NULL DEFAULT 'USD'
);

CREATE TABLE refunds_daily (
    refund_id VARCHAR PRIMARY KEY,
    order_id VARCHAR NOT NULL REFERENCES finance_orders_daily(order_id),
    customer_id VARCHAR NOT NULL REFERENCES customer_dim(customer_id),
    refund_date DATE NOT NULL,
    refund_amount NUMERIC(12, 2) NOT NULL,
    reason VARCHAR
);

CREATE TABLE subscriptions_daily (
    subscription_id VARCHAR NOT NULL,
    customer_id VARCHAR NOT NULL REFERENCES customer_dim(customer_id),
    snapshot_date DATE NOT NULL,
    plan VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    mrr_amount NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (subscription_id, snapshot_date)
);

CREATE TABLE raw_payments (
    payment_id VARCHAR PRIMARY KEY,
    order_id VARCHAR NOT NULL,
    customer_id VARCHAR NOT NULL,
    payment_date DATE NOT NULL,
    event_type VARCHAR NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    gateway VARCHAR NOT NULL,
    is_sandbox BOOLEAN NOT NULL DEFAULT false
);

INSERT INTO customer_dim (customer_id, signup_date, region, segment, is_test) VALUES
    ('cust-001', '2026-01-05', 'NA', 'smb', false),
    ('cust-002', '2026-02-11', 'EMEA', 'mid_market', false),
    ('cust-003', '2026-03-20', 'APAC', 'enterprise', false),
    ('cust-999', '2026-01-01', 'NA', 'smb', true);

INSERT INTO finance_orders_daily (order_id, customer_id, order_date, status, gross_amount, tax_amount) VALUES
    ('ord-001', 'cust-001', '2026-06-01', 'completed', 1200.00, 96.00),
    ('ord-002', 'cust-002', '2026-06-03', 'completed', 4500.00, 360.00),
    ('ord-003', 'cust-003', '2026-06-10', 'completed', 9800.00, 784.00),
    ('ord-004', 'cust-001', '2026-06-15', 'refunded', 500.00, 40.00),
    ('ord-005', 'cust-999', '2026-06-16', 'completed', 100.00, 8.00);

INSERT INTO refunds_daily (refund_id, order_id, customer_id, refund_date, refund_amount, reason) VALUES
    ('ref-001', 'ord-004', 'cust-001', '2026-06-20', 500.00, 'customer request');

INSERT INTO subscriptions_daily (subscription_id, customer_id, snapshot_date, plan, status, mrr_amount) VALUES
    ('sub-001', 'cust-001', '2026-06-30', 'growth', 'active', 300.00),
    ('sub-002', 'cust-002', '2026-06-30', 'enterprise', 'active', 1500.00),
    ('sub-003', 'cust-003', '2026-06-30', 'enterprise', 'active', 2500.00);

-- Deliberately plausible but analytically unsafe alternative. One order may
-- have multiple gateway events, and authorization/sandbox rows are not
-- recognized revenue.
INSERT INTO raw_payments (
    payment_id, order_id, customer_id, payment_date, event_type, amount, gateway, is_sandbox
) VALUES
    ('pay-001-auth', 'ord-001', 'cust-001', '2026-06-01', 'authorization', 1200.00, 'stripe', false),
    ('pay-001-cap', 'ord-001', 'cust-001', '2026-06-01', 'capture', 1200.00, 'stripe', false),
    ('pay-002-cap-a', 'ord-002', 'cust-002', '2026-06-03', 'partial_capture', 2000.00, 'stripe', false),
    ('pay-002-cap-b', 'ord-002', 'cust-002', '2026-06-04', 'partial_capture', 2500.00, 'stripe', false),
    ('pay-999-sandbox', 'ord-005', 'cust-999', '2026-06-16', 'capture', 100.00, 'stripe', true);
