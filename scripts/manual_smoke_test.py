"""
manual_smoke_test.py — Run this yourself, locally, once you've:
  1. pip install -r requirements.txt
  2. created a real .env file with GOOGLE_API_KEY set
  3. run db/init_db.py at least once

This is NOT part of the automated unit test suite (those don't need a
live API key or network access). This script actually talks to Gemini and
walks through a few representative conversation turns, so you can see
real output and catch any LangGraph/Gemini API mismatches early — before
building Phase 4/5 on top of this.

Usage:
    python3 scripts/manual_smoke_test.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

if not os.environ.get("GOOGLE_API_KEY"):
    print("GOOGLE_API_KEY is not set. Create a .env file from .env.example first.")
    sys.exit(1)

from agent.graph import get_memory_snapshot, run_turn  # noqa: E402

SCRIPTED_TURNS = [
    "What vegan options do you have?",
    "I'd like to book a table",
    "My name is Karim, party of 4",
    "2026-06-25 at 19:00",
    "yes",
    "What's your cancellation policy?",
    "asdkjasldj random nonsense unrelated request",
]


def run_scripted_conversation(session_id, turns, label):
    print("\n" + "=" * 70)
    print(label)
    print("=" * 70)
    for turn_number, user_message in enumerate(turns, start=1):
        print(f"\n[Turn {turn_number}] User: {user_message}")
        try:
            reply = run_turn(session_id, user_message)
            print(f"[Turn {turn_number}] Assistant: {reply}")
            snapshot = get_memory_snapshot(session_id)
            print(f"[Turn {turn_number}] Working memory: {snapshot}")
        except Exception as exc:
            print(f"[Turn {turn_number}] ERROR: {exc!r}")
            print(
                "If this is an import or API-shape error from langgraph, check your "
                "installed langgraph version against agent/graph.py — the StateGraph "
                "API has changed between minor versions before."
            )
            raise


def main():
    run_scripted_conversation(
        "manual-smoke-test",
        SCRIPTED_TURNS,
        "Manual smoke test — scripted conversation against the real graph",
    )

    # Phase 4 bonus: "Marc Abou Jaoude" already has a seeded visit history
    # (db/init_db.py) — this is a SEPARATE session_id on purpose, since
    # user_name is meant to represent "who I'm talking to this session"
    # and shouldn't flip mid-conversation just because a different name
    # gets mentioned later in the same thread.
    run_scripted_conversation(
        "manual-smoke-test-returning-customer",
        ["I'd like to book a table", "My name is Marc Abou Jaoude"],
        "Long-term memory demo — returning customer recognition",
    )

    print("\n" + "=" * 70)
    print("Done. Check logs/agent_trace.jsonl for the full structured trace.")
    print("=" * 70)


if __name__ == "__main__":
    main()
