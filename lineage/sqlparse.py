"""Lightweight SQL parsing helpers for lineage extraction.

Handles a realistic warehouse-SQL subset (BigQuery/Snowflake/Postgres style):

    WITH cte AS (SELECT ...), cte2 AS (...)
    SELECT a.id, COALESCE(b.amount, 0) AS amount, c.name AS customer
    FROM orders a
    JOIN customers c ON a.customer_id = c.id
    LEFT JOIN refunds b ON b.order_id = a.id

Limitations are deliberate and documented: no schema catalog, so unqualified
column references are resolved best-effort (single source -> that source;
otherwise marked unresolved). Wildcards (`*`) propagate as `table.*`.
"""

import re

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
QUALIFIED_REF = re.compile(rf"\b({IDENT})\.({IDENT})(?:\.({IDENT}))?\b")
BARE_WORD = re.compile(rf"\b({IDENT})\b")

SQL_KEYWORDS = {
    "select", "from", "where", "join", "left", "right", "inner", "outer",
    "full", "cross", "on", "using", "group", "order", "having", "limit",
    "offset", "union", "intersect", "except", "with", "as", "distinct",
    "all", "and", "or", "not", "in", "is", "null", "like", "between",
    "case", "when", "then", "else", "end", "cast", "over", "partition",
    "by", "asc", "desc", "true", "false", "interval",
}

FROM_TERMINATORS = ("where", "group", "order", "having", "limit", "offset",
                    "union", "intersect", "except", "window")


def strip_comments(sql: str) -> str:
    """Remove -- line comments and /* block comments, respecting quotes."""
    out, i, n = [], 0, len(sql)
    quote = None
    while i < n:
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote and sql[i - 1] != "\\":
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def depth_at(sql: str, pos: int) -> int:
    """Paren depth at position `pos`, ignoring quoted strings."""
    depth, quote, i = 0, None, 0
    while i < pos:
        ch = sql[i]
        if quote:
            if ch == quote and sql[i - 1] != "\\":
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        i += 1
    return depth


def split_top_level(sql: str, sep: str) -> list:
    """Split on `sep` (single char) at paren depth 0, respecting quotes."""
    parts, buf, depth, quote = [], [], 0, None
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if quote:
            buf.append(ch)
            if ch == quote and sql[i - 1] != "\\":
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
        elif ch == "(":
            depth += 1
            buf.append(ch)
            i += 1
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    parts.append("".join(buf))
    return parts


def find_top_level(sql: str, keyword: str) -> int:
    """Index of keyword as a whole word at depth 0, or -1."""
    for m in re.finditer(rf"\b{re.escape(keyword)}\b", sql, re.IGNORECASE):
        if depth_at(sql, m.start()) == 0:
            return m.start()
    return -1


def find_first_of_top_level(sql: str, keywords) -> int:
    """Earliest index among keywords at depth 0, or -1."""
    best = -1
    for kw in keywords:
        idx = find_top_level(sql, kw)
        if idx != -1 and (best == -1 or idx < best):
            best = idx
    return best


def extract_ctes(sql: str):
    """Return (list of (name, body), remaining_sql) for a WITH clause.

    Only handles CTEs at the start of the statement.
    """
    ctes = []
    rest = sql.strip()
    m = re.match(r"\bWITH\b", rest, re.IGNORECASE)
    if not m:
        return ctes, rest
    rest = rest[m.end():].strip()
    while True:
        m = re.match(rf"({IDENT})\s+AS\s*\(", rest, re.IGNORECASE)
        if not m:
            break
        name = m.group(1)
        # find matching close paren for the opening paren just matched
        start = m.end() - 1
        depth, quote, i = 0, None, start
        while i < len(rest):
            ch = rest[i]
            if quote:
                if ch == quote and rest[i - 1] != "\\":
                    quote = None
            elif ch in ("'", '"', "`"):
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = rest[start + 1:i]
        ctes.append((name, body))
        rest = rest[i + 1:].lstrip()
        if rest.startswith(","):
            rest = rest[1:].lstrip()
            continue
        break
    return ctes, rest


def parse_select_items(select_part: str):
    """Parse the comma-separated items after SELECT into (alias, expr)."""
    items = []
    for raw in split_top_level(select_part, ","):
        item = raw.strip()
        if not item:
            continue
        alias, expr = split_alias(item)
        items.append((alias, expr))
    return items


def split_alias(item: str):
    """Split `expr [AS] alias` at the top level. Returns (alias, expr).

    When no alias is present, the alias is derived from the expression:
    a simple `t.col` reference -> `col`; otherwise the raw expression.
    """
    m = re.search(r"\bAS\b", item, re.IGNORECASE)
    if m and depth_at(item, m.start()) == 0:
        return item[m.end():].strip().strip('"`'), item[:m.start()].strip()
    # bare trailing alias: `SUM(x) revenue` — but not `f(x)` or `t.col`
    m2 = re.match(rf"^(.*\S)\s+({IDENT})$", item, re.DOTALL)
    if m2 and depth_at(item, m2.start(2)) == 0:
        expr_part = m2.group(1).rstrip()
        if not expr_part.endswith(("(", ".", ",")):
            return m2.group(2), expr_part
    # no alias: derive one
    q = QUALIFIED_REF.search(item)
    if q and q.group(0).strip() == item.strip():
        return (q.group(3) or q.group(2)), item.strip()
    b = BARE_WORD.fullmatch(item.strip())
    if b and b.group(1).lower() not in SQL_KEYWORDS:
        return b.group(1), item.strip()
    return item.strip(), item.strip()


def _subquery_spans(sql: str):
    """(start, end) spans of parenthesized SELECT/WITH subqueries."""
    spans = []
    i, n = 0, len(sql)
    while i < n:
        if sql[i] == "(":
            j = i + 1
            while j < n and sql[j].isspace():
                j += 1
            if re.match(r"(?:SELECT|WITH)\b", sql[j:], re.IGNORECASE):
                depth, quote, k = 0, None, i
                while k < n:
                    ch = sql[k]
                    if quote:
                        if ch == quote and sql[k - 1] != "\\":
                            quote = None
                    elif ch in ("'", '"', "`"):
                        quote = ch
                    elif ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                spans.append((i, k))
                i = k
        i += 1
    return spans


def column_refs(expr: str):
    """All column references in an expression: qualified `a.b` and bare `b`.

    References inside nested subqueries are skipped (they belong to the
    subquery's own scope); function-call arguments are included.
    """
    spans = _subquery_spans(expr)

    def in_subquery(pos):
        return any(s <= pos < e for s, e in spans)

    refs = []
    seen = set()
    for m in QUALIFIED_REF.finditer(expr):
        if in_subquery(m.start()):
            continue
        parts = [p for p in m.groups() if p]
        col = parts[-1]
        qualifier = ".".join(parts[:-1])
        key = (qualifier, col)
        if key not in seen and col.lower() not in SQL_KEYWORDS:
            seen.add(key)
            refs.append((qualifier, col))
    # mask qualified refs, then find bare words not followed by `(` and not keywords
    masked = QUALIFIED_REF.sub(" ", expr)
    for m in BARE_WORD.finditer(masked):
        word = m.group(1)
        if word.lower() in SQL_KEYWORDS:
            continue
        if in_subquery(m.start()):
            continue
        rest = masked[m.end():].lstrip()
        if rest.startswith("("):  # function name
            continue
        if re.fullmatch(r"\d+(\.\d+)?", word):  # numeric literal
            continue
        key = (None, word)
        if key not in seen:
            seen.add(key)
            refs.append((None, word))
    return refs


def parse_from_sources(from_part: str):
    """Parse FROM/JOIN sources into [(alias, table_or_subquery)].

    `table_or_subquery` is either a table name string or a ("subquery", sql)
    tuple. The alias falls back to the table name when omitted.
    """
    # cut trailing clauses
    cut = find_first_of_top_level(from_part, FROM_TERMINATORS)
    if cut != -1:
        from_part = from_part[:cut]
    sources = []
    # split on top-level commas and JOIN keywords
    chunks = split_top_level(from_part, ",")
    expanded = []
    for chunk in chunks:
        expanded.extend(
            re.split(r"\b(?:LEFT|RIGHT|INNER|OUTER|FULL|CROSS)?\s*JOIN\b",
                     chunk, flags=re.IGNORECASE)
        )
    for chunk in expanded:
        chunk = chunk.strip()
        if not chunk:
            continue
        # drop ON/USING condition
        for kw in ("on", "using"):
            idx = find_top_level(chunk, kw)
            if idx != -1:
                chunk = chunk[:idx].strip()
                break
        if not chunk or chunk.upper().startswith("LATERAL"):
            continue
        m = re.match(rf"^\(({IDENT})?\s*$", chunk)  # placeholder guard
        if chunk.startswith("("):
            # derived table: (SELECT ...) [AS] alias
            depth, quote, i = 0, None, 0
            end = -1
            while i < len(chunk):
                ch = chunk[i]
                if quote:
                    if ch == quote and chunk[i - 1] != "\\":
                        quote = None
                elif ch in ("'", '"', "`"):
                    quote = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
                i += 1
            sub = chunk[1:end]
            tail = chunk[end + 1:].strip()
            am = re.match(rf"^(?:AS\s+)?({IDENT})$", tail, re.IGNORECASE)
            alias = am.group(1) if am else "subq"
            sources.append((alias, ("subquery", sub)))
        else:
            parts = chunk.split()
            if not parts:
                continue
            table = parts[0].strip('"`')
            alias = table
            if len(parts) >= 2:
                if parts[1].upper() == "AS" and len(parts) >= 3:
                    alias = parts[2].strip('"`')
                elif parts[1].upper() not in SQL_KEYWORDS:
                    alias = parts[1].strip('"`')
            alias = alias.split(".")[-1]
            sources.append((alias, table))
    return sources
