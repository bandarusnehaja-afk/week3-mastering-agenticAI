"""Shared service layer. `run_weekly_report` drives the deterministic LangGraph pipeline;
the query helpers below it (`get_current_blockers`, `get_stuck_tasks`, ...) read straight
from SQLite memory and back the report renderer, the conversational agent's tools, and the
Streamlit dashboard — "the same set of tools" behind every interface."""

from collections import Counter
from typing import Optional

from . import config
from .connectors import asana, jira, notion
from .memory.store import MemoryStore

_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore(config.DB_PATH)
    return _store


def fetch_all_tasks(sprint_name: Optional[str], store: MemoryStore):
    latest = store.latest_run()
    prev_jira = store.get_snapshot_tasks_by_source(latest["run_id"], "jira") if latest else []
    prev_asana = store.get_snapshot_tasks_by_source(latest["run_id"], "asana") if latest else []
    prev_notion = store.get_snapshot_tasks_by_source(latest["run_id"], "notion") if latest else []

    tasks = []
    tasks += jira.fetch_tasks(sprint_name, previous_tasks=prev_jira)
    tasks += asana.fetch_tasks(sprint_name, previous_tasks=prev_asana)
    tasks += notion.fetch_tasks(sprint_name, previous_tasks=prev_notion)
    return tasks


def run_weekly_report(sprint_name: Optional[str] = None, narrative: bool = False) -> dict:
    from .graph import run_weekly_report_graph
    store = get_store()
    return run_weekly_report_graph(sprint_name=sprint_name, narrative=narrative, store=store)


# --- query helpers shared by the report renderer and the chat tools ---

def get_current_blockers() -> list:
    store = get_store()
    latest = store.latest_run()
    if not latest:
        return []
    return [dict(r) for r in store.get_snapshot(latest["run_id"]) if r["blocked"]]


def get_stuck_tasks(min_sprints: int = 2) -> list:
    store = get_store()
    return [dict(r) for r in store.get_stuck_tasks(min_runs=min_sprints)]


def get_task_history(task_id: str) -> list:
    store = get_store()
    return [dict(r) for r in store.get_task_history(task_id)]


def compare_last_two_runs() -> Optional[dict]:
    store = get_store()
    runs = store.list_runs(limit=2)
    if len(runs) < 2:
        return None
    later, earlier = runs[0], runs[1]
    diff = store.diff_runs(earlier["run_id"], later["run_id"])
    out = {}
    for key, items in diff.items():
        if key == "status_changed":
            out[key] = [(dict(a), dict(b)) for a, b in items]
        else:
            out[key] = [dict(r) for r in items]
    return out


def get_latest_snapshot() -> list:
    store = get_store()
    latest = store.latest_run()
    if not latest:
        return []
    return [dict(r) for r in store.get_snapshot(latest["run_id"])]


# --- read helpers for the Streamlit dashboard (browsing arbitrary past runs) ---

def list_runs(limit: int = 20) -> list:
    store = get_store()
    return [dict(r) for r in store.list_runs(limit=limit)]


def get_snapshot_for_run(run_id: int) -> list:
    store = get_store()
    return [dict(r) for r in store.get_snapshot(run_id)]


def compare_run_to_previous(run_id: int) -> Optional[dict]:
    store = get_store()
    prev = store.latest_run(before_run_id=run_id)
    if not prev:
        return None
    diff = store.diff_runs(prev["run_id"], run_id)
    out = {}
    for key, items in diff.items():
        if key == "status_changed":
            out[key] = [(dict(a), dict(b)) for a, b in items]
        else:
            out[key] = [dict(r) for r in items]
    return out


def get_risk_trend(limit: int = 10) -> list:
    """Risk-level counts per run, oldest first, for charting trend over time."""
    store = get_store()
    runs = list(reversed(store.list_runs(limit=limit)))
    trend = []
    for r in runs:
        counts = Counter(row["risk_level"] for row in store.get_snapshot(r["run_id"]))
        trend.append({
            "run_id": r["run_id"],
            "label": f"#{r['run_id']} {r['sprint_name'] or ''}".strip(),
            "high": counts.get("high", 0),
            "medium": counts.get("medium", 0),
            "low": counts.get("low", 0),
            "total": sum(counts.values()),
        })
    return trend
