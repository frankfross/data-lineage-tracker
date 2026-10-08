"""Build column-level lineage from SQL text.

Each SQL file is treated as one model: the file stem (e.g. `orders_daily`)
is the target table. CTEs and derived tables are resolved recursively, and
when several models are built together, references to another *model* are
inlined too — so a column of `orders_daily` traces all the way back to the
base tables, not just to the intermediate model.
"""

from .graph import LineageGraph
from . import sqlparse


def _model_columns(sql: str):
    """Parse one SELECT statement -> {output_col: set of `table.column`}."""
    sql = sqlparse.strip_comments(sql).strip().rstrip(";").strip()
    ctes, rest = sqlparse.extract_ctes(sql)

    # resolve CTEs in order (later CTEs may reference earlier ones).
    # _model_columns already resolves each body against its own CTE scope,
    # so the outputs here are final `table.column` strings.
    cte_outputs = {}  # cte name -> {out_col: set(upstream `table.column`)}
    for name, body in ctes:
        cte_outputs[name.lower()] = _model_columns(body)

    from_idx = sqlparse.find_top_level(rest, "select")
    if from_idx == -1:
        return {}
    select_part = rest[from_idx + len("select"):]
    from_at = sqlparse.find_top_level(select_part, "from")
    if from_at == -1:
        return {alias: set() for alias, _ in sqlparse.parse_select_items(select_part)}

    items = sqlparse.parse_select_items(select_part[:from_at])
    from_part = select_part[from_at + len("from"):]
    sources = sqlparse.parse_from_sources(from_part)

    # map alias -> ("cte", outputs) | ("table", name) | ("subquery", outputs)
    source_map = {}
    for alias, src in sources:
        if isinstance(src, tuple) and src[0] == "subquery":
            source_map[alias.lower()] = ("subquery", _model_columns(src[1]))
        elif isinstance(src, str) and src.lower() in cte_outputs:
            source_map[alias.lower()] = ("cte", cte_outputs[src.lower()])
        else:
            source_map[alias.lower()] = ("table", src if isinstance(src, str) else alias)

    return {
        alias: _resolve_refs(sqlparse.column_refs(expr), cte_outputs, source_map)
        for alias, expr in items
    }


def _resolve_refs(refs, cte_outputs, source_map):
    """Turn `(qualifier, column)` refs into final `table.column` names.

    CTE/subquery qualifiers are inlined to their own upstreams, so lineage
    always terminates at base tables (or `?.col` when unresolvable).
    """
    out = set()
    for qualifier, col in refs:
        if qualifier is None:
            out.update(_resolve_bare(col, source_map))
            continue
        key = qualifier.lower()
        if key in source_map:
            kind, payload = source_map[key]
            if kind in ("cte", "subquery"):
                ups = payload.get(col, payload.get(col.lower(), set()))
                out.update(ups)
            else:
                out.add(f"{payload}.{col}")
        elif key in cte_outputs:
            out.update(cte_outputs[key].get(col, cte_outputs[key].get(col.lower(), set())))
        else:
            out.add(f"{qualifier}.{col}")
    return out


def _resolve_bare(col, source_map):
    """Best-effort resolution of an unqualified column reference."""
    if not source_map:
        return {f"?.{col}"}
    if len(source_map) == 1:
        (alias, (kind, payload)), = source_map.items()
        if kind == "table":
            return {f"{payload}.{col}"}
        ups = payload.get(col, payload.get(col.lower()))
        return set(ups) if ups else {f"{alias}.{col}"}
    # multiple sources: look for exactly one CTE/subquery exposing the column
    candidates = [
        (alias, payload) for alias, (kind, payload) in source_map.items()
        if kind in ("cte", "subquery") and (col in payload or col.lower() in payload)
    ]
    if len(candidates) == 1:
        alias, payload = candidates[0]
        return set(payload.get(col, payload.get(col.lower())))
    return {f"?.{col}"}


def _inline_models(raw):
    """Replace `model.col` upstreams with that model's own upstreams.

    Iterates to a fixpoint (bounded, so model cycles terminate with the
    cycle left unresolved rather than looping forever).
    """
    for _ in range(len(raw) + 1):
        changed = False
        for name, cols in raw.items():
            for out_col, ups in cols.items():
                new_ups = set()
                for up in ups:
                    tbl, _, col = up.rpartition(".")
                    key = tbl.lower()
                    if key in raw and key != name.lower() and not tbl.startswith("?"):
                        target = raw[key]
                        inlined = target.get(col, target.get(col.lower()))
                        if inlined:
                            new_ups.update(inlined)
                            changed = True
                            continue
                    new_ups.add(up)
                cols[out_col] = new_ups
        if not changed:
            break
    return raw


def build_model(sql: str, model_name: str) -> LineageGraph:
    """Lineage graph for one model; nodes are `table.column`."""
    return build_many({model_name: sql})


def build_many(models: dict) -> LineageGraph:
    """Merge lineage graphs for {model_name: sql_text}, chaining models."""
    raw = {name: _model_columns(sql) for name, sql in models.items()}
    raw = _inline_models({k.lower(): v for k, v in raw.items()})
    graph = LineageGraph()
    for name, cols in raw.items():
        for out_col, upstreams in cols.items():
            target = f"{name}.{out_col}"
            if not upstreams:
                graph.add_node(target)  # constant / literal-only column
            for up in upstreams:
                graph.add_edge(up, target)
    return graph
