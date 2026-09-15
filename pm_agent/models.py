from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


@dataclass
class Task:
    id: str                    # source-native id, e.g. "ENG-101"
    source: str                 # "jira" | "asana" | "notion"
    title: str
    status: str                 # normalized: todo | in_progress | in_review | blocked | done
    raw_status: str              # original status string from the source
    assignee: Optional[str]
    priority: Optional[str]
    sprint: Optional[str]
    due_date: Optional[date]
    blocked: bool
    blocked_reason: Optional[str]
    updated_at: datetime
    url: Optional[str] = None


@dataclass
class TaskRisk:
    task: Task
    risk_level: str              # "low" | "medium" | "high"
    reasons: List[str] = field(default_factory=list)
