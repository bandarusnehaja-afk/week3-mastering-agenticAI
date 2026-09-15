"""Asana connector. Uses the real Asana REST API when ASANA_ACCESS_TOKEN and
ASANA_PROJECT_GID are set; otherwise falls back to mock data. Asana has no built-in
"status" concept beyond complete/incomplete, so this looks for a "blocked" tag and an
optional custom field named "Status" to approximate todo/in_progress/in_review/blocked."""

import os
from datetime import date, datetime
from typing import List, Optional

import requests

from ..models import Task


def is_configured() -> bool:
    return all(os.getenv(k) for k in ("ASANA_ACCESS_TOKEN", "ASANA_PROJECT_GID"))


def fetch_tasks(sprint: Optional[str] = None, previous_tasks: Optional[List[Task]] = None) -> List[Task]:
    if is_configured():
        return _fetch_live(sprint)
    from .mock_data import evolve_or_seed
    return evolve_or_seed(source="asana", previous_tasks=previous_tasks, sprint_name=sprint or "Sprint 24")


def _map_status(value: str) -> str:
    v = value.lower()
    if "block" in v:
        return "blocked"
    if "review" in v:
        return "in_review"
    if "progress" in v:
        return "in_progress"
    if "done" in v or "complete" in v:
        return "done"
    return "todo"


def _fetch_live(sprint: Optional[str]) -> List[Task]:
    token = os.environ["ASANA_ACCESS_TOKEN"]
    project_gid = os.environ["ASANA_PROJECT_GID"]
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "opt_fields": "name,completed,assignee.name,due_on,modified_at,"
                       "tags.name,custom_fields.name,custom_fields.display_value,permalink_url",
    }
    resp = requests.get(
        f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks",
        headers=headers, params=params, timeout=20,
    )
    resp.raise_for_status()

    tasks = []
    for t in resp.json().get("data", []):
        tag_names = [tg["name"].lower() for tg in t.get("tags", [])]
        blocked = "blocked" in tag_names
        cf_status = None
        for cf in t.get("custom_fields", []) or []:
            if (cf.get("name") or "").lower() == "status" and cf.get("display_value"):
                cf_status = cf["display_value"]

        if t.get("completed"):
            status = "done"
        elif blocked:
            status = "blocked"
        elif cf_status:
            status = _map_status(cf_status)
        else:
            status = "in_progress"

        due = date.fromisoformat(t["due_on"]) if t.get("due_on") else None
        tasks.append(Task(
            id=t["gid"], source="asana", title=t["name"],
            status=status, raw_status=cf_status or ("Complete" if t.get("completed") else "Incomplete"),
            assignee=(t.get("assignee") or {}).get("name"),
            priority=None, sprint=sprint, due_date=due,
            blocked=blocked, blocked_reason="Tagged 'blocked' in Asana" if blocked else None,
            updated_at=datetime.strptime(t["modified_at"][:19], "%Y-%m-%dT%H:%M:%S"),
            url=t.get("permalink_url"),
        ))
    return tasks
