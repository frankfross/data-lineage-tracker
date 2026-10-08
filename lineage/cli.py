"""CLI: point at a directory of .sql files, get column lineage as JSON + DOT.

Each file is one model named after its stem:
    python -m lineage.cli --sql-dir examples --out lineage.json --dot lineage.dot
"""

import argparse
import sys
from pathlib import Path

from .builder import build_many


def main(argv=None):
    parser = argparse.ArgumentParser(description="Column-level SQL lineage tracker")
    parser.add_argument("--sql-dir", required=True, help="directory of .sql model files")
    parser.add_argument("--out", default="lineage.json", help="JSON output path")
    parser.add_argument("--dot", default=None, help="Graphviz DOT output path")
    parser.add_argument("--print", action="store_true", help="print JSON to stdout")
    args = parser.parse_args(argv)

    sql_dir = Path(args.sql_dir)
    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        print(f"no .sql files found in {sql_dir}", file=sys.stderr)
        return 1

    models = {f.stem: f.read_text() for f in files}
    graph = build_many(models)

    Path(args.out).write_text(graph.to_json())
    if args.dot:
        Path(args.dot).write_text(graph.to_dot())
    if args.print:
        print(graph.to_json())
    print(f"models={len(models)} nodes={len(graph.nodes)} edges={len(graph.edges)}")
    print(f"wrote {args.out}" + (f" and {args.dot}" if args.dot else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
