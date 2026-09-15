"""Blocker/risk detection. Combines the current task state with the previous week's
snapshot (from SQLite memory) to flag tasks that are explicitly blocked, overdue, stale,
unassigned, or unchanged since last week."""

from datetime import date, datetime, timedelta
from typing import List, Optional

from .config import STALE_DAYS
from .memory.store import MemoryStore
from .models import Task, TaskRisk


def score_task(task: Task, previous_row: Optional[dict] = None) -> TaskRisk:
    reasons: List[str] = []
    level = "low"

    if task.blocked:
        reasons.append(task.blocked_reason or "Marked as blocked")
        level = "high"

    if task.due_date and task.status != "done" and task.due_date < date.today():
        reasons.append(f"Overdue since {task.due_date.isoformat()}")
        level = "high"

    if task.status == "in_progress":
        age = datetime.utcnow() - task.updated_at
        if age > timedelta(days=STALE_DAYS):
            reasons.append(f"No update in {age.days} days")
            level = "high" if level == "high" else "medium"

    if not task.assignee and task.status not in ("todo", "done"):
        reasons.append("No assignee")
        if level == "low":
            level = "medium"

    if (
        previous_row is not None
        and previous_row["status"] == task.status
        and task.status in ("blocked", "in_progress")
        and bool(previous_row["blocked"]) and task.blocked
    ):
        reasons.append("Unchanged since last week's report")
        level = "high"

    if not reasons:
        reasons.append("On track")

    return TaskRisk(task=task, risk_level=level, reasons=reasons)


def score_tasks(tasks: List[Task], store: MemoryStore, previous_run_id: Optional[int]) -> List[TaskRisk]:
    prev_by_key = {}
    if previous_run_id is not None:
        for row in store.get_snapshot(previous_run_id):
            prev_by_key[(row["task_id"], row["source"])] = row

    return [score_task(t, prev_by_key.get((t.id, t.source))) for t in tasks]
