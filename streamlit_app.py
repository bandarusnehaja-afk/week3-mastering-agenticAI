"""Streamlit UI for the PM Status Agent: a Dashboard tab for the deterministic weekly
report (browsable across past runs, with charts) and a Chat tab for the conversational
ReAct agent. Both read/write through the same pm_agent.service layer and SQLite memory
that the CLI (main.py) uses — there is no separate state here."""

import json

import pandas as pd
import streamlit as st
from langchain_core.messages import HumanMessage, SystemMessage

from pm_agent import service

st.set_page_config(page_title="PM Status Agent", page_icon="📊", layout="wide")

STATUS_LABELS = {
    "todo": "To Do", "in_progress": "In Progress", "in_review": "In Review",
    "blocked": "Blocked", "done": "Done",
}
RISK_LABELS = {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}


@st.cache_resource(show_spinner=False)
def get_chat_agent():
    from pm_agent.chat import build_chat_agent
    return build_chat_agent()


def run_label(r: dict) -> str:
    return f"#{r['run_id']} — {r['sprint_name'] or 'unnamed sprint'} ({r['timestamp'][:16].replace('T', ' ')})"


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------

def render_dashboard():
    st.title("📊 Weekly Status Dashboard")

    with st.sidebar:
        st.header("Generate Report")
        sprint_name = st.text_input("Sprint name", value="Sprint 24")
        narrative = st.checkbox("Include LLM executive summary", value=False)
        if st.button("🔄 Run new weekly report", width="stretch", type="primary"):
            with st.spinner("Fetching Jira / Asana / Notion, scoring risk, saving snapshot..."):
                try:
                    result = service.run_weekly_report(sprint_name=sprint_name or None, narrative=narrative)
                    st.session_state["last_markdown"] = result
                    st.success(f"Report #{result['run_id']} generated.")
                except Exception as e:
                    st.error(f"Failed to generate report: {e}")

    runs = service.list_runs(limit=25)
    if not runs:
        st.info("No report has been generated yet. Use **Run new weekly report** in the sidebar to create one.")
        return

    options = {run_label(r): r["run_id"] for r in runs}
    selected_label = st.selectbox("Viewing report", list(options.keys()), index=0)
    run_id = options[selected_label]

    rows = service.get_snapshot_for_run(run_id)
    df = pd.DataFrame(rows)
    stuck = service.get_stuck_tasks(min_sprints=2)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total tasks", len(df))
    c2.metric("🔴 High risk", int((df["risk_level"] == "high").sum()))
    c3.metric("🟡 Medium risk", int((df["risk_level"] == "medium").sum()))
    c4.metric("⚠️ Stuck 2+ sprints", len(stuck))

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Status breakdown")
        status_counts = df["status"].map(lambda s: STATUS_LABELS.get(s, s)).value_counts()
        st.bar_chart(status_counts)
    with col2:
        st.subheader("Risk breakdown")
        risk_counts = df["risk_level"].map(lambda r: RISK_LABELS.get(r, r)).value_counts()
        st.bar_chart(risk_counts)

    trend = service.get_risk_trend(limit=10)
    if len(trend) > 1:
        st.subheader("Risk trend across runs")
        trend_df = pd.DataFrame(trend).set_index("label")[["high", "medium", "low"]]
        st.line_chart(trend_df)

    st.subheader("🔴 Risk Flags")
    high_df = df[df["risk_level"] == "high"]
    if high_df.empty:
        st.write("No high-risk items in this report.")
    else:
        for _, row in high_df.iterrows():
            reasons = json.loads(row["risk_reasons"]) if row.get("risk_reasons") else []
            header = f"[{row['source'].upper()}] {row['title']} ({row['task_id']}) — {row['assignee'] or 'Unassigned'}"
            with st.expander(header):
                for reason in reasons:
                    st.write(f"- {reason}")
                if row.get("url"):
                    st.markdown(f"[Open in {row['source'].title()}]({row['url']})")

    if stuck:
        st.subheader("⚠️ Stuck for 2+ Sprints")
        stuck_df = pd.DataFrame(stuck)
        stuck_df["status"] = stuck_df["status"].map(lambda s: STATUS_LABELS.get(s, s))
        st.dataframe(
            stuck_df[["source", "title", "task_id", "status", "assignee"]],
            width="stretch", hide_index=True,
        )

    trends = service.compare_run_to_previous(run_id)
    if trends:
        st.subheader("Week-over-Week Changes")
        sections = [
            ("completed", "✅ Completed"), ("newly_blocked", "🆕🔴 Newly Blocked"),
            ("unblocked", "🟢 Unblocked"), ("new_tasks", "➕ New"),
        ]
        tabs = st.tabs([label for _, label in sections])
        for tab, (key, label) in zip(tabs, sections):
            with tab:
                items = trends.get(key) or []
                if not items:
                    st.write("Nothing here.")
                else:
                    for item in items:
                        extra = f" — {item['blocked_reason']}" if key == "newly_blocked" and item.get("blocked_reason") else ""
                        st.write(f"- [{item['source'].upper()}] {item['title']} ({item['task_id']}){extra}")
    else:
        st.caption("No earlier report to compare this one against.")

    st.subheader("Full Task List")
    fc1, fc2, fc3 = st.columns(3)
    sources = sorted(df["source"].unique())
    statuses = sorted(df["status"].unique())
    source_filter = fc1.multiselect("Source", sources, default=sources)
    status_filter = fc2.multiselect("Status", statuses, default=statuses, format_func=lambda s: STATUS_LABELS.get(s, s))
    risk_filter = fc3.multiselect("Risk", ["high", "medium", "low"], default=["high", "medium", "low"], format_func=lambda r: RISK_LABELS.get(r, r))

    filtered = df[df["source"].isin(source_filter) & df["status"].isin(status_filter) & df["risk_level"].isin(risk_filter)].copy()
    filtered["status"] = filtered["status"].map(lambda s: STATUS_LABELS.get(s, s))
    filtered["risk_level"] = filtered["risk_level"].map(lambda r: RISK_LABELS.get(r, r))
    st.dataframe(
        filtered[["source", "title", "task_id", "status", "assignee", "risk_level"]]
        .rename(columns={"task_id": "id", "risk_level": "risk"}),
        width="stretch", hide_index=True,
    )

    if st.session_state.get("last_markdown", {}).get("run_id") == run_id:
        st.download_button(
            "⬇️ Download this report as markdown",
            st.session_state["last_markdown"]["markdown"],
            file_name=f"report_run_{run_id}.md",
        )


# ----------------------------------------------------------------------------
# Chat
# ----------------------------------------------------------------------------

SUGGESTIONS = [
    "What's been stuck for more than one sprint?",
    "What changed since last week?",
    "What's blocked right now?",
]


def render_chat():
    st.title("💬 Ask the PM Agent")
    st.caption("Same tools as the dashboard, backed by the same SQLite memory — ask about status, blockers, or trends.")

    with st.sidebar:
        if st.button("🗑️ Clear conversation", width="stretch"):
            st.session_state.pop("chat_history", None)
            st.rerun()

    try:
        agent = get_chat_agent()
    except RuntimeError as e:
        st.warning(f"Chat is unavailable: {e}")
        return
    except Exception as e:
        st.error(f"Unexpected error building the chat agent: {e}")
        return

    if "chat_history" not in st.session_state:
        from pm_agent.chat import SYSTEM_PROMPT
        st.session_state["chat_history"] = [SystemMessage(content=SYSTEM_PROMPT)]

    for msg in st.session_state["chat_history"]:
        if isinstance(msg, SystemMessage):
            continue
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        with st.chat_message(role):
            st.markdown(msg.content)

    clicked = None
    if len(st.session_state["chat_history"]) == 1:  # only the system prompt so far
        st.write("Try asking:")
        cols = st.columns(len(SUGGESTIONS))
        for col, suggestion in zip(cols, SUGGESTIONS):
            if col.button(suggestion, width="stretch"):
                clicked = suggestion

    question = st.chat_input("Ask about sprint status, blockers, or trends...") or clicked
    if question:
        st.session_state["chat_history"].append(HumanMessage(content=question))
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    result = agent.invoke({"messages": st.session_state["chat_history"]})
                    st.session_state["chat_history"] = result["messages"]
                    st.markdown(result["messages"][-1].content)
                except Exception as e:
                    st.session_state["chat_history"].pop()  # drop the unanswered question so it can be retried
                    st.error(f"Something went wrong: {e}")


# ----------------------------------------------------------------------------

def main():
    page = st.sidebar.radio("Navigate", ["📊 Dashboard", "💬 Chat"])
    st.sidebar.markdown("---")
    if page.startswith("📊"):
        render_dashboard()
    else:
        render_chat()


if __name__ == "__main__":
    main()
