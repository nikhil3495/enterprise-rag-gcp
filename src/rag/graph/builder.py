"""LangGraph wiring.

START → route ─┬─(vector)→ retrieve ─────────────┐
               ├─(sql)───→ run_sql ──────────────┤
               └─(both)──→ retrieve ∥ run_sql ───┴→ grade ─┬→ generate → check_grounding ─┬→ END
                               ▲                           │        ▲                      │
                               └──────── rewrite ←─────────┘        └── (not grounded, once)┘
"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from rag.graph import nodes
from rag.graph.nodes import GraphDeps
from rag.graph.state import RAGState


def build_graph(deps: GraphDeps) -> Any:
    g: StateGraph[RAGState, Any, Any, Any] = StateGraph(RAGState)
    for name in ("route", "retrieve", "run_sql", "grade", "rewrite", "generate", "check_grounding"):
        g.add_node(name, partial(getattr(nodes, name), deps=deps))

    g.add_edge(START, "route")
    g.add_conditional_edges("route", nodes.after_route, ["retrieve", "run_sql"])
    g.add_edge("retrieve", "grade")
    g.add_edge("run_sql", "grade")
    g.add_conditional_edges("grade", partial(nodes.after_grade, deps=deps), ["generate", "rewrite"])
    g.add_edge("rewrite", "retrieve")
    g.add_edge("generate", "check_grounding")
    g.add_conditional_edges(
        "check_grounding", partial(nodes.after_grounding, deps=deps), {"generate": "generate", "end": END}
    )
    return g.compile()


def mermaid(deps: GraphDeps) -> str:
    return str(build_graph(deps).get_graph().draw_mermaid())
