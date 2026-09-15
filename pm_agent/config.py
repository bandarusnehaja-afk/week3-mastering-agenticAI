import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("PM_AGENT_DB_PATH", str(BASE_DIR / "data" / "memory.sqlite3"))
REPORTS_DIR = Path(os.getenv("PM_AGENT_REPORTS_DIR", str(BASE_DIR / "reports")))
STALE_DAYS = int(os.getenv("PM_AGENT_STALE_DAYS", "7"))


def get_llm():
    """Factory for the LLM used by the conversational agent and the optional
    narrative summary. Anthropic by default; set LLM_PROVIDER=openai to switch."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in "
                "(or set LLM_PROVIDER=openai and OPENAI_API_KEY instead)."
            )
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"), temperature=0)
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o"), temperature=0)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
