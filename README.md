# data-lineage-tracker

Parse a directory of SQL model files and build a **column-level lineage
graph**: every output column traced back to the base-table columns that
feed it. Emits JSON for downstream tooling and Graphviz DOT for
visualization. Zero runtime dependencies — the parser is a small
hand-rolled, quote/paren-aware tokenizer, not a vendored grammar. Portfolio
project.

## Layout

```
lineage/
  sqlparse.py  # comment stripping, top-level splitting, CTE/FROM/SELECT parsing
  builder.py   # recursive CTE/subquery resolution + cross-model inlining
  graph.py     # LineageGraph: JSON and DOT emitters
  cli.py       # CLI: --sql-dir -> lineage.json + lineage.dot
examples/
  orders_enriched.sql  # CTE + joins -> enriched orders
  orders_daily.sql     # aggregates the enriched model (chains across files)
tests/                 # pytest: parser unit tests + end-to-end lineage tests
```

## Quickstart

```bash
pip install -r requirements.txt   # pytest only; the tool itself is stdlib-only
python -m lineage.cli --sql-dir examples --out lineage.json --dot lineage.dot
```

Example output (`models=2 nodes=15 edges=9`):

```json
{
  "nodes": ["dim_customers.country", "raw_orders.amount_cents", "..."],
  "edges": [
    {"from": "raw_orders.amount_cents", "to": "orders_enriched.amount_usd"},
    {"from": "raw_orders.amount_cents", "to": "orders_daily.total_usd"},
    {"from": "dim_customers.name", "to": "orders_enriched.customer_name"}
  ]
}
```

Render the graph:

```bash
dot -Tpng lineage.dot -o lineage.png
```

## Design notes

- **One file = one model.** The file stem is the target table
  (`orders_enriched.sql` builds table `orders_enriched`).
- **CTEs and derived tables resolve recursively.** `WITH paid AS (...)`
  is inlined, so `orders_enriched.amount_usd` traces to
  `raw_orders.amount_cents`, not to the intermediate `paid` CTE.
- **Models chain across files.** `orders_daily` reads from
  `orders_enriched`; the builder inlines the upstream model's columns to a
  fixpoint, so lineage terminates at base tables.
- **Honest about ambiguity.** Unqualified columns (`SELECT id` with several
  joined tables) can't be attributed without a schema catalog, so they're
  emitted as `?.id` (dashed in DOT) instead of guessed. `SELECT *`
  propagates as `table.*`.
- **Supported subset:** CTEs, subqueries in FROM, INNER/LEFT/RIGHT/FULL/CROSS
  JOINs, table aliases, `expr AS alias` and bare aliases, qualified
  `t.col` references. Window functions, `PIVOT`, and dialect-specific DDL
  are out of scope by design.

## Tests

```bash
python -m pytest tests/ -q   # 13 tests: parser units + end-to-end lineage
```
