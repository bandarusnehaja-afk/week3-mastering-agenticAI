"""Conversational Q&A interface. Same tools as the deterministic report (tools.py), but
here an LLM (via LangGraph's prebuilt ReAct agent) decides which ones to call and how to
answer, e.g. 'What's been stuck for more than one sprint?'"""

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from . import config
from .tools import ALL_TOOLS

SYSTEM_PROMPT = """You are a program-management assistant with live access to the team's \
Jira, Asana, and Notion tracked work through tools, plus a persistent memory of every past \
weekly status report. Answer questions about current sprint status, blockers, and \
week-over-week trends precisely and concisely, citing task IDs. If a question needs data \
you don't have yet (e.g. no report has run this week), say so and suggest running one. \
Never invent task names, statuses, or IDs — only report what the tools return."""


def build_chat_agent():
    llm = config.get_llm()
    return create_react_agent(llm, ALL_TOOLS)


def _seed_history():
    return [SystemMessage(content=SYSTEM_PROMPT)]


def ask_once(question: str) -> str:
    agent = build_chat_agent()
    result = agent.invoke({"messages": _seed_history() + [HumanMessage(content=question)]})
    return result["messages"][-1].content


def run_repl():
    agent = build_chat_agent()
    print("PM Status Agent — ask about sprint status, blockers, or trends. Ctrl+C to exit.\n")
    history = _seed_history()
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        history.append(HumanMessage(content=question))
        result = agent.invoke({"messages": history})
        history = result["messages"]
        print(f"\nagent> {history[-1].content}\n")
