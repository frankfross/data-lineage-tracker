"""Column-level lineage graph: nodes are `table.column`, edges are upstream->down."""

import json


class LineageGraph:
    def __init__(self):
        self.nodes = set()
        self.edges = set()  # (upstream, downstream)

    def add_edge(self, upstream: str, downstream: str):
        self.nodes.add(upstream)
        self.nodes.add(downstream)
        self.edges.add((upstream, downstream))

    def add_node(self, node: str):
        self.nodes.add(node)

    def merge(self, other: "LineageGraph"):
        self.nodes |= other.nodes
        self.edges |= other.edges

    def to_dict(self):
        return {
            "nodes": sorted(self.nodes),
            "edges": [{"from": u, "to": d} for u, d in sorted(self.edges)],
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent)

    def to_dot(self):
        lines = ["digraph lineage {", '  rankdir="LR";',
                 '  node [shape=box, style=rounded, fontname="Helvetica"];']
        for node in sorted(self.nodes):
            table, _, col = node.rpartition(".")
            label = f"{table}\\n{col}" if table else col
            if node.startswith("?."):
                lines.append(f'  "{node}" [label="{label}", style="rounded,dashed", color="gray50"];')
            else:
                lines.append(f'  "{node}" [label="{label}"];')
        for u, d in sorted(self.edges):
            lines.append(f'  "{u}" -> "{d}";')
        lines.append("}")
        return "\n".join(lines) + "\n"
