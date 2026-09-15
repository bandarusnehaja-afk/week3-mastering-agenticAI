"""SQLite-backed memory layer. Every weekly report run writes a full snapshot of every
tracked task (with its computed risk level) into `snapshots`, linked to a `runs` row. This
is what lets the agent answer trend questions ("what's changed since last week", "what's
been stuck for 2+ sprints") without re-fetching anything from Jira/Asana/Notion."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date as _date, datetime as _dt
from pathlib import Path
from typing import List, Optional

from ..models import Task, TaskRisk

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    sprint_name TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    task_id TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_status TEXT,
    assignee TEXT,
    priority TEXT,
    sprint TEXT,
    due_date TEXT,
    blocked INTEGER NOT NULL,
    blocked_reason TEXT,
    risk_level TEXT NOT NULL,
    risk_reasons TEXT,
    updated_at TEXT,
    url TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_task ON snapshots(task_id, source);
CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id);
"""


class MemoryStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_run(self, sprint_name: Optional[str]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO runs (timestamp, sprint_name) VALUES (?, ?)",
                (_dt.utcnow().isoformat(), sprint_name),
            )
            return cur.lastrowid

    def save_snapshot(self, run_id: int, scored: List[TaskRisk]) -> None:
        rows = []
        for tr in scored:
            t = tr.task
            rows.append((
                run_id, t.id, t.source, t.title, t.status, t.raw_status,
                t.assignee, t.priority, t.sprint,
                t.due_date.isoformat() if t.due_date else None,
                int(t.blocked), t.blocked_reason,
                tr.risk_level, json.dumps(tr.reasons),
                t.updated_at.isoformat() if t.updated_at else None,
                t.url,
            ))
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO snapshots
                (run_id, task_id, source, title, status, raw_status, assignee, priority,
                 sprint, due_date, blocked, blocked_reason, risk_level, risk_reasons,
                 updated_at, url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def latest_run(self, before_run_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        with self._conn() as conn:
            if before_run_id is not None:
                cur = conn.execute(
                    "SELECT * FROM runs WHERE run_id < ? ORDER BY run_id DESC LIMIT 1",
                    (before_run_id,),
                )
            else:
                cur = conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1")
            return cur.fetchone()

    def list_runs(self, limit: int = 20) -> List[sqlite3.Row]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,))
            return cur.fetchall()

    def get_snapshot(self, run_id: int) -> List[sqlite3.Row]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM snapshots WHERE run_id = ?", (run_id,))
            return cur.fetchall()

    def get_snapshot_tasks_by_source(self, run_id: int, source: str) -> List[Task]:
        rows = [r for r in self.get_snapshot(run_id) if r["source"] == source]
        return [_row_to_task(r) for r in rows]

    def get_task_history(self, task_id: str) -> List[sqlite3.Row]:
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT snapshots.*, runs.timestamp AS run_timestamp, runs.sprint_name AS run_sprint_name
                   FROM snapshots JOIN runs ON snapshots.run_id = runs.run_id
                   WHERE task_id = ? ORDER BY runs.run_id ASC""",
                (task_id,),
            )
            return cur.fetchall()

    def get_stuck_tasks(self, min_runs: int = 2, statuses=("blocked", "in_progress")) -> List[sqlite3.Row]:
        """Tasks that sat in a non-done, non-todo status for the `min_runs` most recent runs."""
        runs = self.list_runs(limit=min_runs)
        if len(runs) < min_runs:
            return []
        run_ids = [r["run_id"] for r in runs]  # most recent first
        with self._conn() as conn:
            placeholders = ",".join("?" * len(run_ids))
            cur = conn.execute(f"SELECT * FROM snapshots WHERE run_id IN ({placeholders})", run_ids)
            rows = cur.fetchall()

        by_task = {}
        for r in rows:
            by_task.setdefault(r["task_id"], {})[r["run_id"]] = r

        stuck = []
        for task_id, by_run in by_task.items():
            if not all(rid in by_run for rid in run_ids):
                continue
            statuses_seen = [by_run[rid]["status"] for rid in run_ids]
            if all(s in statuses for s in statuses_seen):
                stuck.append(by_run[run_ids[0]])
        return stuck

    def diff_runs(self, run_id_a: int, run_id_b: int) -> dict:
        """Diff two runs: b (later) vs a (earlier)."""
        a = {r["task_id"]: r for r in self.get_snapshot(run_id_a)}
        b = {r["task_id"]: r for r in self.get_snapshot(run_id_b)}
        return {
            "new_tasks": [b[k] for k in b if k not in a],
            "removed": [a[k] for k in a if k not in b],
            "newly_blocked": [b[k] for k in b if k in a and b[k]["blocked"] and not a[k]["blocked"]],
            "unblocked": [b[k] for k in b if k in a and not b[k]["blocked"] and a[k]["blocked"]],
            "completed": [b[k] for k in b if k in a and b[k]["status"] == "done" and a[k]["status"] != "done"],
            "status_changed": [(a[k], b[k]) for k in b if k in a and a[k]["status"] != b[k]["status"]],
        }


def _row_to_task(r: sqlite3.Row) -> Task:
    return Task(
        id=r["task_id"], source=r["source"], title=r["title"], status=r["status"],
        raw_status=r["raw_status"], assignee=r["assignee"], priority=r["priority"],
        sprint=r["sprint"],
        due_date=_date.fromisoformat(r["due_date"]) if r["due_date"] else None,
        blocked=bool(r["blocked"]), blocked_reason=r["blocked_reason"],
        updated_at=_dt.fromisoformat(r["updated_at"]) if r["updated_at"] else _dt.utcnow(),
        url=r["url"],
    )
