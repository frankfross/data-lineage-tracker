"""Tests for the SQL parsing helpers."""

from lineage import sqlparse as sp


def test_strip_comments():
    sql = "SELECT a -- trailing\n/* block\ncomment */ b"
    assert sp.strip_comments(sql) == "SELECT a \n b"


def test_split_top_level_ignores_parens_and_quotes():
    parts = sp.split_top_level("a, f(x, y), 'p,q', b", ",")
    assert [p.strip() for p in parts] == ["a", "f(x, y)", "'p,q'", "b"]


def test_extract_ctes():
    sql = "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT * FROM b"
    ctes, rest = sp.extract_ctes(sql)
    assert [n for n, _ in ctes] == ["a", "b"]
    assert rest.strip().upper().startswith("SELECT")


def test_select_items_with_aliases():
    items = sp.parse_select_items("o.id, COALESCE(o.amt, 0) AS amount, c.name customer")
    assert items[0] == ("id", "o.id")
    assert items[1] == ("amount", "COALESCE(o.amt, 0)")
    assert items[2] == ("customer", "c.name")


def test_column_refs_qualified_and_bare():
    refs = sp.column_refs("COALESCE(o.amt, 0) + fee AS total")
    quals = {(q, c) for q, c in refs}
    assert ("o", "amt") in quals
    assert (None, "fee") in quals
    # function name and keywords are not refs
    assert all(c.lower() != "coalesce" for _, c in refs)


def test_from_sources_with_joins_and_aliases():
    srcs = sp.parse_from_sources(
        "raw_orders o JOIN dim_customers AS c ON o.id = c.id LEFT JOIN refunds r ON r.oid = o.id"
    )
    assert srcs == [("o", "raw_orders"), ("c", "dim_customers"), ("r", "refunds")]


def test_from_sources_derived_table():
    srcs = sp.parse_from_sources("(SELECT id FROM t) AS sub")
    assert len(srcs) == 1
    alias, (kind, body) = srcs[0]
    assert alias == "sub" and kind == "subquery"
    assert "SELECT id FROM t" in body
