# PM Status Agent

## Executive Summary

Every program manager maintains the same spreadsheet in their head: what's on the sprint,
what's blocked, what's been blocked for *two* sprints and nobody's said anything, and what
changed since last Monday. This agent automates that spreadsheet.

It connects to Jira, Asana, and Notion, normalizes whatever it finds into one task model,
scores each task for risk (blocked, overdue, stale, unassigned, or quietly stuck), and
writes the result into a local SQLite memory that accumulates across every run. On top of
that memory sit two interfaces:

- **`report`** — run it every week and get a deterministic, auditable markdown status report
  with risk flags and week-over-week deltas. No LLM involved unless you opt into a narrative
  summary.
- **`chat`** — ask it anything: *"What's been stuck for more than one sprint?"*, *"Who owns
  the most blocked work?"*, *"What changed since last week?"* — a LangGraph ReAct agent
  answers by calling the same tools the report uses, grounded in the same memory.

**Status**: built and verified end-to-end. Notion is live-tested against a real workspace
(see [Tool Deep Dives](#notion-connector) for two real bugs found and fixed along the way).
Jira and Asana ship with the same live/mock pattern but were validated against realistic mock
data only — plug in credentials and they follow the identical code path Notion does. The
chat agent has been run against both Anthropic and OpenAI models.

## Architecture

```
                    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
                    │    Jira     │  │    Asana    │  │   Notion    │
                    │  connector  │  │  connector  │  │  connector  │
                    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
                           │  live API if creds set, else evolving mock data
                           └────────────────┬┘────────────────┘
                                            ▼
                                  ┌───────────────────┐
                                  │  service.py        │  fetch_all_tasks()
                                  │  (shared layer)     │
                                  └──────────┬──────────┘
                                             ▼
                                  ┌───────────────────┐
                                  │     risk.py         │  score_tasks()
                                  │  (blocker/risk      │
                                  │   detection)         │
                                  └──────────┬──────────┘
                                             ▼
                                  ┌───────────────────┐
                                  │  memory/store.py     │  SQLite: runs + snapshots
                                  │  (persistent memory) │  trend diffs, stuck-task query
                                  └──────────┬──────────┘
                                             │
                        ┌────────────────────┴────────────────────┐
                        ▼                                         ▼
           ┌─────────────────────────┐              ┌──────────────────────────┐
           │   graph.py (LangGraph)   │              │   tools.py                │
           │   StateGraph:             │              │   6 LangChain @tool       │
           │   fetch→score→store→      │              │   wrappers over the       │
           │   trends→report           │              │   same service.py calls   │
           │   (no LLM in this path)   │              └────────────┬─────────────┘
           └────────────┬─────────────┘                            ▼
                        ▼                              ┌──────────────────────────┐
           ┌─────────────────────────┐                │   chat.py                  │
           │  report_template.py      │                │   LangGraph ReAct agent     │
           │  deterministic markdown  │                │   (create_react_agent)      │
           └────────────┬─────────────┘                └────────────┬─────────────┘
                        ▼                                            ▼
                 `python main.py report`                     `python main.py chat`
                        ▲                                            ▲
                        └──────────────────┬─────────────────────────┘
                                 `streamlit run streamlit_app.py`
                          (Dashboard tab calls graph.py's run_weekly_report()
                           + read-only service.py helpers; Chat tab calls the
                           exact same chat.build_chat_agent())
```

All three entry points — `report`, `chat`, and the Streamlit app — terminate in the same
`service.py` functions and the same SQLite database. There is exactly one source of truth for
task state, so none of the three interfaces can drift out of sync with each other.

```
pm_agent/
  connectors/          Jira, Asana, Notion — real REST API if creds are set,
                        otherwise realistic mock data that evolves week to week
    jira.py, asana.py, notion.py, mock_data.py
  memory/store.py       SQLite: runs + snapshots tables, trend/stuck-task queries
  risk.py                Blocker/risk scoring (blocked, overdue, stale, unassigned,
                          unchanged-since-last-week)
  service.py              Shared query layer used by both interfaces
  graph.py                 LangGraph StateGraph for the deterministic report pipeline
  report_template.py        Markdown rendering (pure, deterministic)
  tools.py                   LangChain @tool wrappers around service.py
  chat.py                     LangGraph ReAct agent over tools.py
main.py                        CLI: `report` and `chat` subcommands
streamlit_app.py                Streamlit UI: Dashboard + Chat tabs over the same service.py
```

### Why SQLite instead of mem0

The spec calls for a memory layer the agent can query for week-over-week trends and answer
questions like "what's stuck." SQLite gives that directly — structured snapshots, joinable
history, exact trend diffs — with zero external dependencies. mem0 is a better fit for
fuzzy/semantic recall over unstructured notes; this agent's memory is entirely structured
task data (task ID, status, risk, timestamp), so a relational store is the more precise and
simpler match. `service.py` is the only place a second, semantic backend would need to plug
in if you wanted mem0 *alongside* this for, say, free-text meeting notes.

## Tools List: LLM vs. Non-LLM

Most of the system runs with **no LLM anywhere in the loop** — fetching, normalizing,
scoring, and persisting data is plain deterministic Python. The LLM only enters at the edges:
deciding *which* tool to call in the chat agent, and optionally writing one prose paragraph.

| Component | Runs on an LLM? | Called by |
|---|---|---|
| `jira.fetch_tasks`, `asana.fetch_tasks`, `notion.fetch_tasks` | No | `service.fetch_all_tasks` (graph node `fetch`) |
| `risk.score_task` / `score_tasks` | No | graph node `score` |
| `MemoryStore.create_run` / `save_snapshot` | No | graph node `store` |
| `MemoryStore.diff_runs` / `get_stuck_tasks` | No | graph node `trends` |
| `report_template.render_report` | No | graph node `report` |
| `report_template._llm_narrative` | **Yes — single completion, no tool-calling** | `render_report`, only with `--narrative` |
| `tools.get_current_sprint_snapshot` | **Yes — LLM decides to call it** | chat agent |
| `tools.get_current_blockers` | **Yes — LLM decides to call it** | chat agent |
| `tools.get_stuck_tasks` | **Yes — LLM decides to call it** | chat agent |
| `tools.get_task_history` | **Yes — LLM decides to call it** | chat agent |
| `tools.compare_last_two_reports` | **Yes — LLM decides to call it** | chat agent |
| `tools.run_new_weekly_report` | **Yes — LLM decides to call it, but the report itself is generated by the same non-LLM graph** | chat agent |
| `service.list_runs` / `get_snapshot_for_run` / `compare_run_to_previous` / `get_risk_trend` | No | Streamlit **Dashboard** tab only |
| Streamlit **Chat** tab | **Yes — same `create_react_agent` as `main.py chat`, same 6 tools** | `streamlit_app.py::render_chat` |

The distinction that matters: the LLM never computes a status, a risk level, or a trend
itself — it only decides *which already-computed, deterministic tool to call* and then
paraphrases the result. This is what keeps the report auditable and prevents the agent from
inventing a task ID or a status that doesn't exist in memory.

## Tool Deep Dives

### Connectors (non-LLM)

Each connector exposes one function, `fetch_tasks(sprint, previous_tasks) -> List[Task]`,
and picks live vs. mock based on whether its required env vars are set (`is_configured()`).

- **Jira** (`pm_agent/connectors/jira.py`) — Jira Agile API to resolve the active sprint
  (`/rest/agile/1.0/board/{id}/sprint`), then `/rest/api/3/search` with a JQL query scoped to
  that sprint/project. Status is mapped via a lowercase lookup table (`"in review"` →
  `in_review`, etc.); a task is `blocked` if its Jira status maps to blocked *or* it carries
  the `flagged` field *or* a `blocked` label.
- **Asana** (`pm_agent/connectors/asana.py`) — `GET /projects/{gid}/tasks` from the Asana
  REST API. Asana has no native "status" concept beyond complete/incomplete, so this reads an
  optional custom field named `Status` and a `blocked` tag as a fallback signal.
- **Notion** (`pm_agent/connectors/notion.py`) — `POST /v1/databases/{id}/query`, paginated.
  Reads `Status`, `Assignee`/`Owner`, `Priority`, and `Due date`/`Due` properties.

  **Two real bugs found and fixed while live-testing against an actual workspace:**
  1. Notion has *two* different property types that look identical in the UI: the legacy
     `select` type and the newer native `status` type (with its "To-do / In progress /
     Complete" groups). The original code only read `select`, so every task silently
     defaulted to "To Do" regardless of its real status. Fixed by reading `select` *or*
     `status` in `_prop_select`.
  2. Property-name lookup was case-sensitive (`"Priority"` ≠ `"priority"`). Real databases
     are inconsistently capitalized. Fixed with a case-insensitive `_ci_get` lookup.

  A third, structural bug: `.env` was only loaded as a side effect of importing
  `pm_agent.config`. Any code path that imported a connector directly (e.g. a script or a
  test) without importing `config` first would silently fall back to mock data even with
  valid credentials, because `os.environ` was never populated. Fixed by moving
  `load_dotenv()` into `pm_agent/__init__.py`, so it always runs on first import of the
  package, regardless of which submodule triggers it.

- **Mock fallback** (`mock_data.py`) — not random noise. `evolve_or_seed` mutates the
  *previous* snapshot for a source (pulled from SQLite) so that running the report repeatedly
  produces believable trends: tasks progress through the pipeline, some get blocked, and some
  deliberately **stay** blocked across runs so `get_stuck_tasks` has something real to find.

### `risk.py` — blocker/risk scoring (non-LLM)

`score_task(task, previous_row)` returns a `TaskRisk(level, reasons)`. Rules, in the order
applied:

| Condition | Effect |
|---|---|
| `task.blocked` is true | risk → `high`, reason = blocked_reason |
| `due_date` in the past and status ≠ done | risk → `high`, reason = "Overdue since …" |
| status = `in_progress` and no update in `PM_AGENT_STALE_DAYS` (default 7) | risk → at least `medium`, `high` if already high |
| no `assignee` and status not in (`todo`, `done`) | risk → at least `medium` |
| same status as last snapshot, still blocked/in-progress, still blocked both times | risk → `high`, reason = "Unchanged since last week's report" |
| none of the above | `low`, reason = "On track" |

This is pure comparison logic against the current `Task` and the previous run's row from
SQLite — no LLM judgment call about what counts as risky.

### `memory/store.py` — SQLite memory (non-LLM)

Two tables: `runs` (one row per `report` invocation) and `snapshots` (one row per task per
run, including its computed risk). Key queries:

- **`get_stuck_tasks(min_runs=2)`** — pulls the `min_runs` most recent runs, groups snapshot
  rows by `task_id`, and returns any task whose status was in `{blocked, in_progress}` in
  *every one* of those runs. This is the literal implementation behind "what's been stuck for
  more than one sprint."
- **`diff_runs(run_a, run_b)`** — set-differences two runs' task ID sets and status/blocked
  fields to produce `new_tasks`, `removed`, `newly_blocked`, `unblocked`, `completed`, and
  `status_changed`. Backs both the report's "Week-over-Week Changes" section and the chat
  tool `compare_last_two_reports`.

### `tools.get_current_sprint_snapshot` (LLM-invoked)

No arguments. Returns every task in the latest snapshot as one formatted block. The LLM
reaches for this on broad questions ("what's on the sprint," "who's working on X") where it
needs the full picture rather than a filtered slice.

### `tools.get_current_blockers` (LLM-invoked)

No arguments. Filters the latest snapshot to `blocked = true`. Verified live:

```
you> What's blocked right now?
- [JIRA] Spike: evaluate vector DB options (ENG-111) — status=blocked, risk=high,
  blocked_reason=Blocked by infra/provisioning ticket
- [ASANA] Coordinate beta customer onboarding calls (OPS-100) — status=blocked, risk=high,
  blocked_reason=Blocked by infra/provisioning ticket
- [NOTION] Maintain competitive landscape doc (DOC-104) — status=blocked, risk=high,
  blocked_reason=Waiting on legal review
```

### `tools.get_stuck_tasks(min_sprints=2)` (LLM-invoked)

One optional argument the LLM can override (e.g. "stuck for 3+ sprints" → `min_sprints=3`).
Delegates straight to `MemoryStore.get_stuck_tasks`. This is the tool behind the flagship
question in the spec, *"What's been stuck for more than one sprint?"* — verified live to
correctly separate genuinely-stuck blocked/in-progress tasks from tasks that only look risky
in a single snapshot.

### `tools.get_task_history(task_id)` (LLM-invoked)

One required argument, a task ID (e.g. `"ENG-111"`). Returns every snapshot row for that task
across every run, in order — status, blocked flag, and risk level at each point in time. The
LLM calls this for "what's the history on X" or to double-check a claim before answering a
"how long has this been going" question.

### `tools.compare_last_two_reports` (LLM-invoked)

No arguments. Wraps `MemoryStore.diff_runs` over the two most recent runs. Returns `None`
(→ "need at least two weekly reports") if only one run exists yet — the agent surfaces that
limitation instead of guessing.

### `tools.run_new_weekly_report(sprint_name="")` (LLM-invoked, triggers non-LLM work)

The one tool with a side effect: it calls `service.run_weekly_report`, which runs the *entire
deterministic LangGraph pipeline* — live fetch, scoring, a new SQLite snapshot — and returns
the rendered markdown. The LLM decides *whether* to refresh the data ("generate this week's
report," "refresh the status"); it does not touch how that data is computed.

### `streamlit_app.py` — the UI layer (mixed)

Not a tool in the LangChain sense, but the third consumer of `service.py` alongside `report`
and `chat`. Structure:

- **Navigation** — a single script, no Streamlit multipage routing. `st.sidebar.radio` picks
  between `render_dashboard()` and `render_chat()`; each is a plain function that draws into
  the main area and reads/writes `st.session_state`.
- **Dashboard tab (non-LLM)** — every element on this tab is a direct read from `service.py`,
  re-run on every Streamlit interaction (widget changes trigger a full script rerun, which is
  cheap here since it's local SQLite reads, not network calls):
  - `service.list_runs()` populates a run picker (`st.selectbox`), so you can inspect any past
    week, not just the latest — useful for spot-checking a specific report after the fact.
  - `service.get_snapshot_for_run(run_id)` → a `pandas.DataFrame` feeds the metrics
    (`st.metric`), the status/risk `st.bar_chart`s, the filterable `st.dataframe` task table,
    and the expandable risk-flag cards (`st.expander`, with `risk_reasons` — stored as a JSON
    string column in SQLite — parsed back into a bullet list per task).
  - `service.get_risk_trend(limit=10)` drives an `st.line_chart` of high/medium/low counts
    across the last 10 runs — the one visualization that only makes sense *because* of the
    accumulated memory; a single run has no trend.
  - `service.compare_run_to_previous(run_id)` renders week-over-week deltas as `st.tabs`
    (Completed / Newly Blocked / Unblocked / New), mirroring the CLI report's markdown
    section but interactive.
  - The sidebar's **"Run new weekly report"** button is the one write path: it calls
    `service.run_weekly_report(...)` directly — the exact same deterministic LangGraph
    pipeline the CLI runs — inside an `st.spinner`, then reruns the page so the new run shows
    up in the picker immediately.
- **Chat tab (LLM)** — thin Streamlit wrapper around `pm_agent.chat.build_chat_agent()`:
  `st.session_state["chat_history"]` holds the running `list[BaseMessage]` (seeded with the
  same `SYSTEM_PROMPT` as the CLI), rendered as `st.chat_message` bubbles; `st.chat_input`
  (or one of three suggested-question buttons, shown only before the first message) appends a
  `HumanMessage` and invokes the agent exactly as `chat.ask_once` does. A failed turn pops the
  unanswered question back off history so it can be retried instead of leaving a dangling
  question the agent never answered.
- **Caching** — `build_chat_agent()` is wrapped in `st.cache_resource`, so the LLM client and
  the compiled ReAct graph are built once per server process and reused across reruns *and*
  across browser sessions (it's a global cache, not per-user — fine here since the tools
  themselves are stateless; all per-conversation state lives in `session_state`, not in the
  cached agent).
- **Failure mode** — if `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is missing, `get_chat_agent()`
  raises the same `RuntimeError` the CLI does; the Chat tab catches it and shows `st.warning`
  inline instead of crashing the whole app, so the Dashboard tab keeps working regardless.
- **Verified, not just written** — exercised with Streamlit's `AppTest` framework (executes
  the actual script headlessly and inspects rendered elements), confirming zero exceptions on
  both tabs, correct metric/dataframe/expander counts on the Dashboard, and a full click-through
  round trip on the Chat tab (suggestion button click → real LLM call → real tool call →
  correctly rendered answer), not just that the page loads.

## Prompt Library

Every piece of natural-language text the system sends to an LLM, verbatim.

### 1. Chat agent system prompt (`pm_agent/chat.py`)

Seeds every chat session (REPL and `-q` single-question mode alike):

```
You are a program-management assistant with live access to the team's Jira, Asana, and
Notion tracked work through tools, plus a persistent memory of every past weekly status
report. Answer questions about current sprint status, blockers, and week-over-week trends
precisely and concisely, citing task IDs. If a question needs data you don't have yet (e.g.
no report has run this week), say so and suggest running one. Never invent task names,
statuses, or IDs — only report what the tools return.
```

### 2. Tool descriptions (`pm_agent/tools.py`)

In a LangChain/LangGraph ReAct agent, each `@tool` function's docstring *is* the prompt
fragment the model sees when deciding what to call — these six strings are effectively the
agent's tool-selection prompt, assembled automatically:

| Tool | Description sent to the LLM |
|---|---|
| `get_current_sprint_snapshot` | "Return the most recent weekly report's full task snapshot: every tracked task with its status, assignee, source, and risk level. Use this for 'what's on the sprint', 'who's working on X', or 'what's the status right now'." |
| `get_current_blockers` | "Return every task currently flagged as blocked, with the reason, across Jira, Asana, and Notion. Use this for 'what's blocked right now' or 'what are this week's blockers'." |
| `get_stuck_tasks` | "Return tasks that have stayed blocked or in-progress for at least `min_sprints` consecutive weekly reports (default 2). Use this for 'what's been stuck for more than one sprint' or 'what's not moving'." |
| `get_task_history` | "Return the full week-over-week history of a single task (by its source ID, e.g. 'ENG-101') across every weekly report it has appeared in: status, risk, and blocked reason at each point in time." |
| `compare_last_two_reports` | "Compare the two most recent weekly reports: newly completed tasks, newly blocked tasks, unblocked tasks, and newly added tasks. Use this for 'what changed since last week' or 'how are we trending'." |
| `run_new_weekly_report` | "Fetch fresh data from Jira, Asana, and Notion, score risk, save a new snapshot to memory, and return the generated weekly status report as markdown. Use this when asked to 'generate this week's report' or 'refresh the status'." |

### 3. Narrative summary prompt (`pm_agent/report_template.py::_llm_narrative`)

Only sent when `report --narrative` is passed; a single completion call, not part of a tool
loop:

```
Write a 3-4 sentence executive summary for a program manager, based on these sprint stats
for {sprint_name}: {stats}. Be direct and specific about risk. No preamble, no headers.
```

where `{stats}` is a small dict computed by code (`total_tasks`, `high_risk`,
`stuck_2plus_sprints`, `completed_this_week`, `newly_blocked_this_week`) — the numbers
themselves are never left to the model to compute, only to phrase.

## MCP Integration

**Not currently implemented.** The connectors talk to Jira/Asana/Notion's REST APIs directly
rather than through MCP servers, and the agent's tools aren't exposed as an MCP server. Two
ways this could extend the architecture, if useful:

- **Consuming MCP** — swap the hand-rolled REST connectors for official MCP servers (e.g. an
  Atlassian/Jira MCP server, a Notion MCP server) as the data-fetching layer. Would remove the
  need to hand-maintain REST parsing and credential handling per source, at the cost of an
  extra process hop and less control over exactly which fields get normalized into `Task`.
- **Exposing MCP** — wrap `tools.ALL_TOOLS` behind an MCP server (e.g. with the `mcp` Python
  SDK / FastMCP) so any MCP client — Claude Desktop, Claude Code, another team's agent — could
  call `get_stuck_tasks` or `get_current_blockers` directly, instead of only this project's
  bundled CLI chat. This is the more natural extension: it turns the SQLite memory this agent
  has already built up into a resource other agents can query, without duplicating the fetch/
  score/store pipeline.

Neither is built because the spec's actual data sources (three REST APIs, one SQLite file)
don't need the extra indirection yet — but `tools.py` is already structured as the seam where
either would plug in.

## Alternate Architectures Considered

| Architecture | Description | Why not chosen (or: when it would win) |
|---|---|---|
| **Single ReAct Agent** | One LLM-driven agent with every tool, including live fetch/write, deciding everything — no separate deterministic pipeline. | Simplest mental model, but the *weekly report* stops being reproducible: two runs over the same underlying data could fetch different tool subsets or phrase risk differently, which is disqualifying for something meant to be cron-scheduled and diffed week over week. Also burns LLM cost/latency on every report generation for work that's pure ETL. |
| **Hybrid Direct + ReAct** *(chosen)* | Deterministic LangGraph pipeline for the report; a separate ReAct agent for ad hoc Q&A; both call the same `service.py`/SQLite layer. | Report generation is auditable, free, and safe to run unattended; conversational Q&A gets the flexibility an LLM is actually good at. One shared tool/service layer keeps the two from drifting. Trade-off: two code paths to reason about — mitigated here because both are thin wrappers over `service.py`, not independent implementations. |
| **LangGraph Stateful Graph (full)** | Extend `graph.py` so the *chat* loop is also a graph node with a LangGraph checkpointer (e.g. `SqliteSaver`) for cross-process conversation persistence, and route report-vs-chat as branches of one graph. | Buys native multi-turn state persistence across process restarts and LangGraph Studio replay/inspection. Overkill for a CLI tool where chat history only needs to survive one REPL session — the in-memory message list in `chat.py` is sufficient today. Worth revisiting if this becomes a long-running server serving many users concurrently. |
| **Event-driven microservices** | Jira/Asana/Notion webhooks → ingestion service → queue (Kafka/SQS) → risk-scoring service → memory service (Postgres) → notifier service (Slack/email) on risk transitions; report and chat become separate stateless APIs. | Right architecture for near-real-time alerts across many teams/orgs at scale, and lets different services be owned by different teams. Wrong architecture for a single team's weekly report: the "weekly snapshot" semantics this tool relies on (point-in-time comparison, not eventual consistency) get harder to reason about, and the infra overhead (queue, multiple deployables, service discovery) isn't justified by the actual load (three REST calls, once a week). |
| **Multi-agent supervisor** | A supervisor LLM coordinates per-source sub-agents (JiraAgent, AsanaAgent, NotionAgent) that each reason about their own source before merging. | Would make sense if each source needed nontrivial *reasoning* to interpret (e.g. free-text Jira comments implying a blocker). Here, each source's status is a handful of structured fields — a deterministic REST call and a status-map lookup is strictly more reliable and orders of magnitude cheaper than an LLM per source per run. |

The common thread: every rejected architecture either put an LLM on the critical path of
*computing* a fact (bad — non-reproducible, costly) or added infrastructure the current scale
doesn't need (bad — YAGNI). The chosen hybrid puts the LLM exactly where it adds value:
choosing which already-correct fact to surface, and how to phrase it.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

By default, every connector runs in **mock mode**: no credentials needed. Mock data isn't
random noise — it evolves realistically run over run (tasks progress, some get blocked and
occasionally *stay* blocked across runs), so trend detection has something real to show.

To connect to live tools, set the relevant variables in `.env`:

- **Jira**: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (+ optional `JIRA_PROJECT_KEY`,
  `JIRA_BOARD_ID` to scope to one project/sprint)
- **Asana**: `ASANA_ACCESS_TOKEN`, `ASANA_PROJECT_GID`
- **Notion**: `NOTION_API_KEY` (an internal integration secret — remember to share the target
  database with the integration via `•••` → Connections), `NOTION_DATABASE_ID`

`chat` (and `report --narrative`) need an LLM key: `ANTHROPIC_API_KEY` by default
(`ANTHROPIC_MODEL` defaults to `claude-sonnet-5`), or set `LLM_PROVIDER=openai` +
`OPENAI_API_KEY` + `pip install langchain-openai`.

## Usage

Generate this week's report (prints to stdout, saves to `reports/`):

```bash
python main.py report --sprint "Sprint 24"
python main.py report --sprint "Sprint 24" --narrative   # adds an LLM executive summary
```

Run it again next week — same command — and the report will show newly blocked/unblocked/
completed/added tasks and flag anything stuck 2+ reports in a row.

Ask questions conversationally:

```bash
python main.py chat                                          # REPL
python main.py chat -q "What's been stuck for more than one sprint?"
python main.py chat -q "What changed since last week?"
python main.py chat -q "Give me the history on ENG-111"
```

### Streamlit UI

```bash
streamlit run streamlit_app.py
```

Opens at `http://localhost:8501` by default. If that port's already taken (e.g. by another
Streamlit app), pick a different one:

```bash
streamlit run streamlit_app.py --server.port 8600
```

Two tabs, chosen from the sidebar:

- **📊 Dashboard** — pick any past run from a dropdown (not just the latest) and see metrics
  (total tasks, high/medium risk counts, stuck-task count), a status/risk breakdown chart, a
  risk trend line across every stored run, expandable risk-flag details with links back to
  the source, the stuck-for-2+-sprints table, week-over-week changes as tabs, and a filterable
  full task table. A sidebar form triggers `service.run_weekly_report` (live fetch + scoring +
  a new SQLite snapshot) without leaving the page.
- **💬 Chat** — the same ReAct agent as `main.py chat`, as a chat-bubble UI with suggested
  starter questions and a "clear conversation" reset. Missing LLM credentials show as an
  in-page warning instead of a crash.

Both tabs read/write through `pm_agent/service.py` and the same `data/memory.sqlite3` the CLI
uses — running `python main.py report` and then opening the dashboard shows that same run. See
[`streamlit_app.py` — the UI layer](#streamlit_apppy--the-ui-layer-mixed) under Tool Deep
Dives for how each element is wired.

## Notes

- The demo database lives at `data/memory.sqlite3` (gitignored). Delete it to reset history.
- `main.py report` writes each run's markdown to `reports/report_run_<id>.md`.
- `.env` is gitignored — credentials never get committed.
