"""
show_failures.py — Prints a compact summary of just the failed/error test
cases from evaluation/results.json, so you don't have to scroll through
the full verbose JSON output to see what went wrong.

Usage (from inside restaurant-agent/):
    python evaluation/show_failures.py
"""

import json
from pathlib import Path

RESULTS_PATH = Path(__file__).parent / "results.json"


def main():
    if not RESULTS_PATH.exists():
        print(f"No results found at {RESULTS_PATH}. Run eval_runner.py first.")
        return

    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    failures = [r for r in results if r["verdict"] != "pass"]

    if not failures:
        print("No failures — all test cases passed!")
        return

    print(f"\n{len(failures)} test case(s) did not pass:\n")
    print("=" * 70)

    for r in failures:
        print(f"{r['test_id']} [{r['verdict'].upper()}] — {r['description']}")
        print(f"  Category:        {r['category']}")
        print(f"  Expected intent: {r['expected']['intent']}")
        print(f"  Expected outcome: {r['expected']['outcome']}")
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
        if r["replies"]:
            last_reply = r["replies"][-1]["assistant"]
            print(f"  Last reply: {last_reply[:200]}")
        else:
            print("  Last reply: (none — no replies recorded)")
        print(f"  Notes: {r.get('notes', '')}")
        print("-" * 70)


if __name__ == "__main__":
    main()
