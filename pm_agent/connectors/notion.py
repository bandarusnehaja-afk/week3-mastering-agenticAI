"""Notion connector. Uses the real Notion API when NOTION_API_KEY and NOTION_DATABASE_ID
are set (querying a project-tracker database with Status/Assignee/Due-date properties);
otherwise falls back to mock data."""

import os
from datetime import date, datetime
from typing import List, Optional

import requests

from ..models import Task

NOTION_VERSION = "2022-06-28"


def is_configured() -> bool:
    return all(os.getenv(k) for k in ("NOTION_API_KEY", "NOTION_DATABASE_ID"))


def fetch_tasks(sprint: Optional[str] = None, previous_tasks: Optional[List[Task]] = None) -> List[Task]:
    if is_configured():
        return _fetch_live(sprint)
    from .mock_data import evolve_or_seed
    return evolve_or_seed(source="notion", previous_tasks=previous_tasks, sprint_name=sprint or "Sprint 24")


def _fetch_live(sprint: Optional[str]) -> List[Task]:
    token = os.environ["NOTION_API_KEY"]
    db_id = os.environ["NOTION_DATABASE_ID"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    tasks, cursor = [], None
    while True:
        body = {"start_cursor": cursor} if cursor else {}
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=headers, json=body, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("results", []):
            tasks.append(_parse_page(page, sprint))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return tasks


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


def _prop_title(props: dict) -> str:
    for v in props.values():
        if v.get("type") == "title":
            rich = v.get("title", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            return text or "Untitled"
    return "Untitled"


def _ci_get(props: dict, name: str) -> Optional[dict]:
    """Property name lookup, case-insensitive — different Notion databases capitalize
    property names inconsistently (e.g. 'Priority' vs 'priority')."""
    for key, val in props.items():
        if key.lower() == name.lower():
            return val
    return None


def _prop_select(props: dict, name: str) -> Optional[str]:
    """Reads a `select`-typed property, or Notion's newer native `status`-typed property
    (same {id, name, color} value shape, different property `type`)."""
    p = _ci_get(props, name)
    if not p:
        return None
    val = p.get("select") or p.get("status")
    return val.get("name") if val else None


def _prop_people(props: dict, name: str) -> Optional[str]:
    p = _ci_get(props, name)
    if not p:
        return None
    people = p.get("people", [])
    if not people:
        return None
    person = people[0]
    return person.get("name") or (person.get("person") or {}).get("email")


def _prop_date(props: dict, name: str) -> Optional[date]:
    p = _ci_get(props, name)
    if not p:
        return None
    d = p.get("date")
    if d and d.get("start"):
        return date.fromisoformat(d["start"][:10])
    return None


def _parse_page(page: dict, sprint: Optional[str]) -> Task:
    props = page["properties"]
    title = _prop_title(props)
    status_raw = _prop_select(props, "Status") or "To Do"
    status = _map_status(status_raw)
    assignee = _prop_people(props, "Assignee") or _prop_people(props, "Owner")
    due = _prop_date(props, "Due date") or _prop_date(props, "Due")
    blocked = status == "blocked"
    return Task(
        id=page["id"], source="notion", title=title, status=status, raw_status=status_raw,
        assignee=assignee, priority=_prop_select(props, "Priority"),
        sprint=sprint, due_date=due, blocked=blocked,
        blocked_reason=f"Status set to '{status_raw}' in Notion" if blocked else None,
        updated_at=datetime.strptime(page["last_edited_time"][:19], "%Y-%m-%dT%H:%M:%S"),
        url=page.get("url"),
    )
