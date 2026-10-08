-- orders_daily: aggregate enriched orders to one row per day
SELECT
    order_date,
    customer_country,
    COUNT(*) AS order_count,
    SUM(amount_usd) AS total_usd,
    AVG(amount_usd) AS avg_usd
FROM orders_enriched
GROUP BY order_date, customer_country;
