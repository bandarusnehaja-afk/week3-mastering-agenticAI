"""Jira connector. Uses the real Jira Cloud REST/Agile API when JIRA_BASE_URL, JIRA_EMAIL,
and JIRA_API_TOKEN are set; otherwise falls back to mock data so the rest of the agent can
run end to end without credentials."""

import os
from datetime import datetime, date
from typing import List, Optional

import requests

from ..models import Task

STATUS_MAP = {
    "to do": "todo", "open": "todo", "backlog": "todo", "selected for development": "todo",
    "in progress": "in_progress", "in development": "in_progress",
    "in review": "in_review", "code review": "in_review", "peer review": "in_review",
    "blocked": "blocked", "on hold": "blocked", "impediment": "blocked",
    "done": "done", "closed": "done", "resolved": "done",
}


def is_configured() -> bool:
    return all(os.getenv(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))


def fetch_tasks(sprint: Optional[str] = None, previous_tasks: Optional[List[Task]] = None) -> List[Task]:
    if is_configured():
        return _fetch_live(sprint)
    from .mock_data import evolve_or_seed
    return evolve_or_seed(source="jira", previous_tasks=previous_tasks, sprint_name=sprint or "Sprint 24")


def _fetch_live(sprint: Optional[str]) -> List[Task]:
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
    board_id = os.getenv("JIRA_BOARD_ID")
    project_key = os.getenv("JIRA_PROJECT_KEY")

    sprint_id = None
    if board_id:
        resp = requests.get(
            f"{base}/rest/agile/1.0/board/{board_id}/sprint",
            params={"state": "active"}, auth=auth, timeout=20,
        )
        resp.raise_for_status()
        sprints = resp.json().get("values", [])
        if sprints:
            sprint_id = sprints[0]["id"]
            sprint = sprint or sprints[0]["name"]

    jql_parts = []
    if project_key:
        jql_parts.append(f'project = "{project_key}"')
    jql_parts.append(f"sprint = {sprint_id}" if sprint_id else "sprint in openSprints()")
    jql = " AND ".join(jql_parts)

    resp = requests.get(
        f"{base}/rest/api/3/search",
        params={
            "jql": jql, "maxResults": 100,
            "fields": "summary,status,assignee,priority,duedate,updated,labels,flagged",
        },
        auth=auth, timeout=20,
    )
    resp.raise_for_status()

    tasks = []
    for issue in resp.json().get("issues", []):
        f = issue["fields"]
        raw_status = f["status"]["name"]
        status = STATUS_MAP.get(raw_status.lower(), "in_progress")
        labels = [l.lower() for l in f.get("labels", [])]
        flagged = bool(f.get("flagged")) or "blocked" in labels
        blocked = status == "blocked" or flagged
        due = date.fromisoformat(f["duedate"]) if f.get("duedate") else None
        tasks.append(Task(
            id=issue["key"], source="jira", title=f["summary"],
            status="blocked" if blocked else status, raw_status=raw_status,
            assignee=(f.get("assignee") or {}).get("displayName"),
            priority=((f.get("priority") or {}).get("name") or "").lower() or None,
            sprint=sprint, due_date=due,
            blocked=blocked, blocked_reason="Flagged as blocked in Jira" if blocked else None,
            updated_at=datetime.strptime(f["updated"][:19], "%Y-%m-%dT%H:%M:%S"),
            url=f"{base}/browse/{issue['key']}",
        ))
    return tasks
