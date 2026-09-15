"""LangGraph pipeline for the deterministic weekly report: fetch -> score -> persist to
memory -> compute trends -> render markdown. Each node is a plain function over explicit
state, so the run is reproducible and auditable — no LLM is on this path unless the final
node is asked for a narrative summary."""

from typing import Any, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from . import service
from .report_template import render_report
from .risk import score_tasks


class ReportState(TypedDict, total=False):
    sprint_name: Optional[str]
    narrative: bool
    store: Any
    previous_run_id: Optional[int]
    tasks: List[Any]
    scored: List[Any]
    run_id: int
    trends: Optional[dict]
    stuck: list
    markdown: str


def _fetch_node(state: ReportState) -> dict:
    store = state["store"]
    previous = store.latest_run()
    tasks = service.fetch_all_tasks(state.get("sprint_name"), store)
    return {"tasks": tasks, "previous_run_id": previous["run_id"] if previous else None}


def _score_node(state: ReportState) -> dict:
    scored = score_tasks(state["tasks"], state["store"], state.get("previous_run_id"))
    return {"scored": scored}


def _store_node(state: ReportState) -> dict:
    store = state["store"]
    run_id = store.create_run(state.get("sprint_name"))
    store.save_snapshot(run_id, state["scored"])
    return {"run_id": run_id}


def _trends_node(state: ReportState) -> dict:
    store = state["store"]
    prev = state.get("previous_run_id")
    trends = store.diff_runs(prev, state["run_id"]) if prev is not None else None
    stuck = store.get_stuck_tasks(min_runs=2)
    return {"trends": trends, "stuck": stuck}


def _report_node(state: ReportState) -> dict:
    md = render_report(
        state["scored"], state.get("trends"), state.get("stuck", []),
        state["run_id"], state.get("sprint_name"), narrative=state.get("narrative", False),
    )
    return {"markdown": md}


def build_report_graph():
    g = StateGraph(ReportState)
    g.add_node("fetch", _fetch_node)
    g.add_node("score", _score_node)
    g.add_node("store", _store_node)
    g.add_node("trends", _trends_node)
    g.add_node("report", _report_node)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "score")
    g.add_edge("score", "store")
    g.add_edge("store", "trends")
    g.add_edge("trends", "report")
    g.add_edge("report", END)
    return g.compile()


def run_weekly_report_graph(sprint_name: Optional[str] = None, narrative: bool = False, store=None) -> dict:
    store = store or service.get_store()
    graph = build_report_graph()
    return graph.invoke({"sprint_name": sprint_name, "narrative": narrative, "store": store})
