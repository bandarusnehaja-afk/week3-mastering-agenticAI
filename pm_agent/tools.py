"""LangChain tools shared between the deterministic report pipeline (called directly, see
graph.py) and the conversational agent (called by the LLM, see chat.py). Every tool here
reads from or writes to the same SQLite memory layer via service.py — there is exactly one
source of truth for task state."""

from langchain_core.tools import tool

from . import service


def _rows_to_text(rows) -> str:
    if not rows:
        return "(none)"
    lines = []
    for r in rows:
        lines.append(
            f"- [{r['source'].upper()}] {r['title']} ({r['task_id']}) — status={r['status']}, "
            f"assignee={r['assignee'] or 'unassigned'}, risk={r.get('risk_level', '?')}"
            + (f", blocked_reason={r['blocked_reason']}" if r.get("blocked_reason") else "")
        )
    return "\n".join(lines)


@tool
def get_current_sprint_snapshot() -> str:
    """Return the most recent weekly report's full task snapshot: every tracked task with
    its status, assignee, source, and risk level. Use this for 'what's on the sprint',
    'who's working on X', or 'what's the status right now'."""
    rows = service.get_latest_snapshot()
    if not rows:
        return "No report has been generated yet. Run the weekly report first."
    return _rows_to_text(rows)


@tool
def get_current_blockers() -> str:
    """Return every task currently flagged as blocked, with the reason, across Jira, Asana,
    and Notion. Use this for 'what's blocked right now' or 'what are this week's blockers'."""
    rows = service.get_current_blockers()
    if not rows:
        return "No blocked tasks in the latest report."
    return _rows_to_text(rows)


@tool
def get_stuck_tasks(min_sprints: int = 2) -> str:
    """Return tasks that have stayed blocked or in-progress for at least `min_sprints`
    consecutive weekly reports (default 2). Use this for 'what's been stuck for more than
    one sprint' or 'what's not moving'."""
    rows = service.get_stuck_tasks(min_sprints=min_sprints)
    if not rows:
        return f"No tasks have been stuck for {min_sprints}+ consecutive reports."
    return _rows_to_text(rows)


@tool
def get_task_history(task_id: str) -> str:
    """Return the full week-over-week history of a single task (by its source ID, e.g.
    'ENG-101') across every weekly report it has appeared in: status, risk, and blocked
    reason at each point in time."""
    rows = service.get_task_history(task_id)
    if not rows:
        return f"No history found for task {task_id}."
    lines = [f"History for {task_id}:"]
    for r in rows:
        lines.append(
            f"- {r['run_timestamp'][:10]} ({r['run_sprint_name'] or 'n/a'}): "
            f"status={r['status']}, blocked={bool(r['blocked'])}, risk={r['risk_level']}"
            + (f", reason={r['blocked_reason']}" if r["blocked_reason"] else "")
        )
    return "\n".join(lines)


@tool
def compare_last_two_reports() -> str:
    """Compare the two most recent weekly reports: newly completed tasks, newly blocked
    tasks, unblocked tasks, and newly added tasks. Use this for 'what changed since last
    week' or 'how are we trending'."""
    diff = service.compare_last_two_runs()
    if diff is None:
        return "Need at least two weekly reports to compare trends."
    parts = []
    for key, label in [
        ("completed", "Completed"), ("newly_blocked", "Newly blocked"),
        ("unblocked", "Unblocked"), ("new_tasks", "New tasks"), ("removed", "Removed"),
    ]:
        items = diff.get(key, [])
        if items:
            names = ", ".join(f"{i['title']} ({i['task_id']})" for i in items)
            parts.append(f"{label}: {names}")
    return "\n".join(parts) if parts else "No changes between the last two reports."


@tool
def run_new_weekly_report(sprint_name: str = "") -> str:
    """Fetch fresh data from Jira, Asana, and Notion, score risk, save a new snapshot to
    memory, and return the generated weekly status report as markdown. Use this when asked
    to 'generate this week's report' or 'refresh the status'."""
    result = service.run_weekly_report(sprint_name=sprint_name or None)
    return result["markdown"]


ALL_TOOLS = [
    get_current_sprint_snapshot,
    get_current_blockers,
    get_stuck_tasks,
    get_task_history,
    compare_last_two_reports,
    run_new_weekly_report,
]
