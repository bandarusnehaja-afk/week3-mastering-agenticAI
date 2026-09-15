#!/usr/bin/env python3
import argparse
import sys

from pm_agent import config, service


def cmd_report(args):
    result = service.run_weekly_report(sprint_name=args.sprint, narrative=args.narrative)
    print(result["markdown"])
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.REPORTS_DIR / f"report_run_{result['run_id']}.md"
    out_path.write_text(result["markdown"])
    print(f"\n(saved to {out_path})", file=sys.stderr)


def cmd_chat(args):
    from pm_agent.chat import ask_once, run_repl
    try:
        if args.question:
            print(ask_once(args.question))
        else:
            run_repl()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="pm-agent", description="PM status agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="Generate this week's deterministic status report")
    p_report.add_argument("--sprint", default=None, help="Sprint/cycle name label")
    p_report.add_argument("--narrative", action="store_true", help="Add an LLM-written executive summary")
    p_report.set_defaults(func=cmd_report)

    p_chat = sub.add_parser("chat", help="Ask the agent questions conversationally")
    p_chat.add_argument("-q", "--question", default=None, help="Ask a single question and exit")
    p_chat.set_defaults(func=cmd_chat)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
