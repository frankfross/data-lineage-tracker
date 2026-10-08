"""End-to-end lineage tests: SQL in, graph out."""

import json

from lineage.builder import build_model, build_many
from lineage.graph import LineageGraph


def edge_set(graph):
    return {(e["from"], e["to"]) for e in graph.to_dict()["edges"]}


def test_simple_join_lineage():
    sql = """
    SELECT o.id, c.name AS customer
    FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    g = build_model(sql, "m")
    edges = edge_set(g)
    assert ("orders.id", "m.id") in edges
    assert ("customers.name", "m.customer") in edges


def test_cte_lineage_inlines_to_base_tables():
    sql = """
    WITH paid AS (
        SELECT order_id, amount FROM raw_orders WHERE status = 'paid'
    )
    SELECT p.order_id, p.amount * 1.08 AS with_tax
    FROM paid p
    """
    g = build_model(sql, "enriched")
    edges = edge_set(g)
    # lineage passes through the CTE back to the base table
    assert ("raw_orders.order_id", "enriched.order_id") in edges
    assert ("raw_orders.amount", "enriched.with_tax") in edges
    assert not any("paid." in u for u, _ in edges)


def test_expression_columns_trace_all_inputs():
    sql = "SELECT COALESCE(a.x, b.y, 0) AS z FROM t1 a JOIN t2 b ON a.id = b.id"
    g = build_model(sql, "m")
    edges = edge_set(g)
    assert ("t1.x", "m.z") in edges
    assert ("t2.y", "m.z") in edges


def test_unresolved_bare_column_marked():
    sql = "SELECT id FROM t1 JOIN t2 ON t1.k = t2.k"
    g = build_model(sql, "m")
    edges = edge_set(g)
    assert ("?.id", "m.id") in edges


def test_model_chain_across_files():
    models = {
        "orders_enriched": "SELECT o.id, c.name AS customer FROM orders o JOIN customers c ON o.cid = c.id",
        "orders_daily": "SELECT customer, COUNT(*) AS n FROM orders_enriched GROUP BY customer",
    }
    g = build_many(models)
    edges = edge_set(g)
    assert ("customers.name", "orders_enriched.customer") in edges
    # chained models resolve through the upstream model's columns
    assert ("customers.name", "orders_daily.customer") in edges


def test_json_and_dot_output():
    g = LineageGraph()
    g.add_edge("orders.id", "m.id")
    data = json.loads(g.to_json())
    assert data["edges"] == [{"from": "orders.id", "to": "m.id"}]
    dot = g.to_dot()
    assert "digraph lineage" in dot
    assert '"orders.id" -> "m.id";' in dot
