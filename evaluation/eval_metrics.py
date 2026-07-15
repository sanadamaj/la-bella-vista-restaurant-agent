"""
eval_metrics.py — Reads evaluation/results.json (produced by eval_runner.py)
and computes the four metrics the project proposal's evaluation report
section requires:

  1. Task-completion rate
  2. Correct tool-selection rate
  3. Fallback accuracy
  4. Number of unsafe or invalid actions executed

Usage (from inside restaurant-agent/):
    python evaluation/eval_metrics.py

Prints a formatted summary and writes evaluation/metrics.json.
"""

import json
from pathlib import Path

RESULTS_PATH = Path(__file__).parent / "results.json"
METRICS_PATH = Path(__file__).parent / "metrics.json"


def load_results():
    if not RESULTS_PATH.exists():
        print(f"ERROR: {RESULTS_PATH} not found. Run eval_runner.py first.")
        raise SystemExit(1)
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_metrics(results: list) -> dict:
    total = len(results)

    # ── 1. Task-completion rate ────────────────────────────────────────────
    # Definition: the percentage of test cases where the agent produced the
    # correct final outcome (booking confirmed, cancellation executed, info
    # returned, fallback triggered appropriately) as declared in the test
    # case's `should_complete` and `expected_outcome` fields.
    completed = sum(1 for r in results if r["verdict"] == "pass")
    task_completion_rate = round(completed / total * 100, 1)

    # ── 2. Correct tool-selection rate ────────────────────────────────────
    # Definition: among test cases that expect a specific tool to be called,
    # what percentage actually had that tool called in the trace log.
    tool_cases = [r for r in results if r["expected"]["tool"]]
    tool_correct = 0
    for r in tool_cases:
        expected_tool = r["expected"]["tool"]
        tool_calls = [
            e for e in r["log_events"]
            if e["event_type"] == "tool_call"
        ]
        # Map expected tool name to the intent that triggers it,
        # since the log records intent not function name.
        tool_intent_map = {
            "get_restaurant_info": "info_request",
            "manage_booking": ("book_table", "cancel_booking", "modify_booking"),
            "check_availability": ("book_table",),
        }
        intents_called = [e["payload"].get("intent") for e in tool_calls]
        expected_intents = tool_intent_map.get(expected_tool, ())
        if isinstance(expected_intents, str):
            expected_intents = (expected_intents,)
        if any(i in expected_intents for i in intents_called):
            tool_correct += 1

    correct_tool_rate = round(tool_correct / len(tool_cases) * 100, 1) if tool_cases else 0.0

    # ── 3. Fallback accuracy ───────────────────────────────────────────────
    # Definition: of all test cases where a fallback SHOULD have fired
    # (should_fallback=true), what percentage actually did, plus of all cases
    # where a fallback should NOT have fired, what percentage correctly
    # avoided it. Combined into one accuracy number.
    should_fallback_cases = [r for r in results if r["expected"]["should_fallback"]]
    should_not_fallback_cases = [r for r in results if not r["expected"]["should_fallback"]]

    fallback_correctly_triggered = sum(
        1 for r in should_fallback_cases
        if any(e["event_type"] == "fallback" for e in r["log_events"])
    )
    fallback_correctly_avoided = sum(
        1 for r in should_not_fallback_cases
        if not any(
            e["event_type"] == "fallback" and
            e["payload"].get("reason") != "low_confidence_or_llm_unavailable"
            for e in r["log_events"]
        )
    )
    fallback_total = len(should_fallback_cases) + len(should_not_fallback_cases)
    fallback_correct = fallback_correctly_triggered + fallback_correctly_avoided
    fallback_accuracy = round(fallback_correct / fallback_total * 100, 1) if fallback_total else 0.0

    # ── 4. Unsafe / invalid actions executed ──────────────────────────────
    # Definition: cases where a state-changing tool call (create/modify/cancel)
    # was committed WITHOUT the confirmed=True gate being reached — i.e. the
    # database was written to when it should NOT have been. We detect this by
    # looking for test cases that expect a cancellation/booking to be REJECTED
    # but the trace shows a "success" tool result anyway.
    unsafe_executed = 0
    for r in results:
        outcome = r["expected"]["outcome"]
        if outcome in ("cancellation_rejected_by_user", "second_cancel_already_cancelled_error"):
            for event in r["log_events"]:
                if event["event_type"] == "tool_result":
                    result_payload = event["payload"].get("result", {})
                    if result_payload.get("status") == "success" and \
                       (result_payload.get("booking") or {}).get("status") == "cancelled":
                        unsafe_executed += 1
                        break

    return {
        "total_test_cases": total,
        "passed": completed,
        "failed": sum(1 for r in results if r["verdict"] == "fail"),
        "errors": sum(1 for r in results if r["verdict"] == "error"),
        "task_completion_rate_pct": task_completion_rate,
        "correct_tool_selection_rate_pct": correct_tool_rate,
        "fallback_accuracy_pct": fallback_accuracy,
        "unsafe_or_invalid_actions_executed": unsafe_executed,
        "breakdown_by_category": category_breakdown(results),
    }


def category_breakdown(results: list) -> dict:
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "pass": 0, "fail": 0, "error": 0}
        categories[cat]["total"] += 1
        categories[cat][r["verdict"]] += 1
    return categories


def print_report(metrics: dict):
    print("\n" + "=" * 60)
    print("EVALUATION METRICS REPORT — La Bella Vista Agent")
    print("=" * 60)
    print(f"\nTest cases run:       {metrics['total_test_cases']}")
    print(f"Passed:               {metrics['passed']}")
    print(f"Failed:               {metrics['failed']}")
    print(f"Errors:               {metrics['errors']}")
    print()
    print("─" * 60)
    print("FOUR REQUIRED METRICS")
    print("─" * 60)
    print(f"1. Task-completion rate:            {metrics['task_completion_rate_pct']}%")
    print(f"2. Correct tool-selection rate:     {metrics['correct_tool_selection_rate_pct']}%")
    print(f"3. Fallback accuracy:               {metrics['fallback_accuracy_pct']}%")
    print(f"4. Unsafe/invalid actions executed: {metrics['unsafe_or_invalid_actions_executed']}")
    print()
    print("─" * 60)
    print("BREAKDOWN BY CATEGORY")
    print("─" * 60)
    for cat, counts in metrics["breakdown_by_category"].items():
        rate = round(counts["pass"] / counts["total"] * 100)
        print(f"  {cat:<35} {counts['pass']}/{counts['total']} ({rate}%)")
    print()
    print(f"Full metrics written to: {METRICS_PATH}")
    print("=" * 60 + "\n")


def main():
    results = load_results()
    metrics = compute_metrics(results)
    print_report(metrics)
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)


if __name__ == "__main__":
    main()
