-- orders_enriched: join orders to customers, normalize amounts
WITH paid_orders AS (
    SELECT
        o.order_id,
        o.customer_id,
        COALESCE(o.amount_cents, 0) / 100.0 AS amount_usd,
        o.placed_at
    FROM raw_orders o
    WHERE o.status = 'paid'
)
SELECT
    p.order_id,
    p.amount_usd,
    c.name AS customer_name,
    c.country AS customer_country,
    DATE(p.placed_at) AS order_date
FROM paid_orders p
JOIN dim_customers c ON c.customer_id = p.customer_id;
