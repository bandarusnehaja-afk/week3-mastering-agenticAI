"""Realistic mock task data, used whenever a connector has no live credentials configured.

Rather than generating a fresh random pool on every run, `evolve_or_seed` mutates the
*previous* snapshot for a source (pulled from SQLite memory) so that running the weekly
report repeatedly produces believable week-over-week trends: tasks progress, occasionally
get blocked, occasionally stay blocked across multiple reports (to exercise the "stuck for
more than one sprint" detection), and new work trickles in.
"""

import random
from datetime import date, datetime, timedelta
from typing import List, Optional

from ..models import Task

ASSIGNEES = [
    "Priya Nair", "Marcus Lee", "Jordan Fischer", "Aisha Khan",
    "Tomasz Wolski", "Elena Petrova", "Sam Okafor", "Grace Kim",
]

PRIORITIES = ["low", "medium", "high", "urgent"]

TASK_TITLES = {
    "jira": [
        "Migrate auth service to new token store",
        "Fix pagination bug in search API",
        "Add rate limiting to public API",
        "Upgrade Postgres to v16",
        "Implement webhook retry logic",
        "Refactor billing calculation module",
        "Add integration tests for checkout flow",
        "Reduce cold-start latency on ingest worker",
        "Support multi-region deploys",
        "Fix flaky CI on frontend test suite",
        "Add audit logging for admin actions",
        "Spike: evaluate vector DB options",
    ],
    "asana": [
        "Coordinate beta customer onboarding calls",
        "Finalize Q3 roadmap deck for leadership",
        "Run customer feedback survey for v2 launch",
        "Align with legal on updated ToS language",
        "Plan cross-team launch readiness review",
        "Set up support macros for new feature",
        "Schedule enablement training for sales",
        "Draft press release for public launch",
        "Coordinate localization vendor handoff",
        "Prepare board update slides",
    ],
    "notion": [
        "Write RFC: event schema versioning",
        "Update onboarding runbook",
        "Document incident postmortem process",
        "Draft API deprecation policy",
        "Maintain competitive landscape doc",
        "Write design doc for notifications v2",
        "Update on-call escalation guide",
        "Catalog data retention requirements",
    ],
}

BLOCK_REASONS = [
    "Waiting on design sign-off",
    "Blocked by upstream API change",
    "Waiting on third-party vendor response",
    "Needs security review",
    "Blocked by infra/provisioning ticket",
    "Waiting on stakeholder decision",
    "Dependency task not yet complete",
    "Waiting on legal review",
]

SOURCE_PREFIX = {"jira": "ENG", "asana": "OPS", "notion": "DOC"}


def _task_id(source: str, i: int) -> str:
    return f"{SOURCE_PREFIX[source]}-{100 + i}"


def seed_tasks(source: str, sprint_name: Optional[str]) -> List[Task]:
    titles = TASK_TITLES[source]
    now = datetime.utcnow()
    tasks = []
    for i, title in enumerate(titles):
        status = random.choices(
            ["todo", "in_progress", "in_review", "blocked", "done"],
            weights=[15, 35, 15, 15, 20],
            k=1,
        )[0]
        blocked = status == "blocked"
        due_offset = random.randint(-4, 10)
        tasks.append(Task(
            id=_task_id(source, i),
            source=source,
            title=title,
            status=status,
            raw_status=status.replace("_", " ").title(),
            assignee=random.choice(ASSIGNEES),
            priority=random.choice(PRIORITIES),
            sprint=sprint_name,
            due_date=date.today() + timedelta(days=due_offset),
            blocked=blocked,
            blocked_reason=random.choice(BLOCK_REASONS) if blocked else None,
            updated_at=now - timedelta(days=random.randint(0, 9)),
            url=f"https://example.com/{source}/{_task_id(source, i)}",
        ))
    return tasks


def evolve_tasks(previous_tasks: List[Task], sprint_name: Optional[str]) -> List[Task]:
    now = datetime.utcnow()
    source = previous_tasks[0].source
    evolved = []

    for t in previous_tasks:
        status, blocked, blocked_reason, updated = t.status, t.blocked, t.blocked_reason, t.updated_at
        roll = random.random()

        if status == "done":
            pass  # completed work stays completed
        elif status == "blocked":
            if roll < 0.35:
                status, blocked, blocked_reason, updated = "in_progress", False, None, now
            # otherwise stays blocked, modeling a task stuck across sprints
        elif status == "in_review":
            if roll < 0.55:
                status, updated = "done", now
            elif roll < 0.65:
                status, blocked, blocked_reason, updated = "blocked", True, random.choice(BLOCK_REASONS), now
        elif status == "in_progress":
            if roll < 0.15:
                status, blocked, blocked_reason, updated = "blocked", True, random.choice(BLOCK_REASONS), now
            elif roll < 0.55:
                status, updated = "in_review", now
            elif random.random() < 0.3:
                updated = now
        elif status == "todo":
            if roll < 0.5:
                status, updated = "in_progress", now

        evolved.append(Task(
            id=t.id, source=t.source, title=t.title, status=status,
            raw_status=status.replace("_", " ").title(), assignee=t.assignee,
            priority=t.priority, sprint=sprint_name, due_date=t.due_date,
            blocked=blocked, blocked_reason=blocked_reason, updated_at=updated, url=t.url,
        ))

    if random.random() < 0.4:
        new_idx = len(previous_tasks) + random.randint(0, 5)
        title = random.choice(TASK_TITLES[source])
        evolved.append(Task(
            id=_task_id(source, new_idx), source=source, title=f"{title} (new)",
            status="todo", raw_status="To Do", assignee=random.choice(ASSIGNEES),
            priority=random.choice(PRIORITIES), sprint=sprint_name,
            due_date=date.today() + timedelta(days=random.randint(2, 14)),
            blocked=False, blocked_reason=None, updated_at=now,
            url=f"https://example.com/{source}/{_task_id(source, new_idx)}",
        ))

    return evolved


def evolve_or_seed(source: str, previous_tasks: Optional[List[Task]], sprint_name: Optional[str]) -> List[Task]:
    if previous_tasks:
        return evolve_tasks(previous_tasks, sprint_name)
    return seed_tasks(source, sprint_name)
