"""
eval_runner.py — Executes the 20 test conversations against the real
agent graph and records what actually happened for each one.

Usage (from inside the restaurant-agent/ folder):
    python evaluation/eval_runner.py                 # run only NEW/unfinished tests
    python evaluation/eval_runner.py --retry-failed   # also re-run tests that FAILed last time
    python evaluation/eval_runner.py --redo-all       # ignore existing results, re-run everything

Requires: GOOGLE_API_KEY in .env, pip install -r requirements.txt,
          python db/init_db.py --reset run first (clean database).

Output: evaluation/results.json — saved incrementally after EVERY test,
        not just at the end, so an interrupted run never loses progress.

RESUMABLE BY DESIGN: Google's free-tier daily quota turned out to be far
lower in practice than commonly advertised (as low as 20 requests/day on
some accounts/projects, for BOTH gemini-2.5-flash and gemini-2.5-flash-lite
— this is a per-project cap, not a per-model one). A single run of all 20
conversations can easily need 70-100+ LLM calls, more than one day's quota
on a constrained account. By default, this script SKIPS any test_id that
already has a "pass" verdict in results.json, so you can run it once a day
across several days and it will only attempt whatever's left, until all 20
are done. If a daily-quota error is detected mid-run, the script stops
immediately (rather than burning more time on calls guaranteed to also
fail) and tells you to resume after the quota resets (~midnight Pacific
Time) or tomorrow.
"""

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from agent.graph import run_turn
from agent.logging_utils import LOG_PATH

CASES_PATH = Path(__file__).parent / "test_cases.json"
RESULTS_PATH = Path(__file__).parent / "results.json"
INTER_TEST_SLEEP_SECONDS = 15
EVAL_RUNNER_VERSION = "v3-resumable-2026-06-20"


def load_test_cases():
    with open(CASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_existing_results() -> dict:
    """Returns {test_id: result_dict} for whatever's already in
    results.json, or an empty dict if there's nothing yet / file is
    corrupt (corrupt is treated as 'start fresh' rather than crashing)."""
    if not RESULTS_PATH.exists():
        return {}
    try:
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            existing = json.load(f)
        return {r["test_id"]: r for r in existing}
    except (json.JSONDecodeError, KeyError):
        print(f"WARNING: {RESULTS_PATH} exists but couldn't be parsed — starting fresh.")
        return {}


def save_results(results_by_id: dict):
    """Writes results.json sorted by test_id, so it stays readable across
    multiple resumed runs."""
    ordered = sorted(results_by_id.values(), key=lambda r: r["test_id"])
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, default=str)


def get_log_events_since(since: datetime) -> list:
    events = []
    if not LOG_PATH.exists():
        return events
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_time = datetime.fromisoformat(event["timestamp"])
                if event_time >= since:
                    events.append(event)
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
    return events


def is_daily_quota_exhausted(log_events: list) -> bool:
    """Detects Google's DAILY quota signature specifically (as opposed to
    an ordinary per-minute throttle, which the existing retry-in-
    llm_client.py already handles fine). Once the daily cap is hit, every
    subsequent call will also fail until it resets — so we stop the whole
    run rather than waste time on guaranteed failures."""
    for event in log_events:
        payload_str = json.dumps(event.get("payload", {}))
        if "RequestsPerDay" in payload_str or "PerDayPerProject" in payload_str:
            return True
    return False


def run_one_case(case: dict) -> dict:
    session_id = f"eval-{case['test_id']}-{uuid.uuid4().hex[:8]}"
    replies = []
    error = None
    started_at = datetime.now(timezone.utc)

    try:
        for turn_index, turn in enumerate(case["turns"]):
            if turn_index > 0:
                time.sleep(3)
            reply = run_turn(session_id, turn["content"])
            replies.append({"user": turn["content"], "assistant": reply})
    except Exception as exc:
        error = str(exc)

    time.sleep(0.5)
    log_events = get_log_events_since(started_at)
    verdict = evaluate_verdict(case, replies, log_events, error)
    quota_exhausted = is_daily_quota_exhausted(log_events)

    return {
        "test_id": case["test_id"],
        "category": case["category"],
        "description": case["description"],
        "session_id": session_id,
        "replies": replies,
        "log_events": log_events,
        "error": error,
        "verdict": verdict,
        "daily_quota_hit": quota_exhausted,
        "expected": {
            "intent": case.get("expected_intent"),
            "tool": case.get("expected_tool"),
            "outcome": case.get("expected_outcome"),
            "should_complete": case.get("should_complete"),
            "should_fallback": case.get("should_fallback"),
        },
        "notes": case.get("notes", ""),
    }


def evaluate_verdict(case, replies, log_events, error):
    if error:
        return "error"

    event_types = [e["event_type"] for e in log_events]
    intents_seen = [
        e["payload"].get("intent")
        for e in log_events
        if e["event_type"] == "intent_classified"
    ]
    tool_results = [
        e["payload"].get("result", {})
        for e in log_events
        if e["event_type"] == "tool_result"
    ]
    fallbacks_seen = [e for e in log_events if e["event_type"] == "fallback"]

    expected_intent = case.get("expected_intent")
    should_fallback = case.get("should_fallback", False)
    should_complete = case.get("should_complete", True)
    expected_outcome = case.get("expected_outcome", "")

    if expected_intent and expected_intent not in intents_seen:
        return "fail"

    if should_fallback and not fallbacks_seen:
        return "fail"
    if not should_fallback and should_complete and fallbacks_seen:
        return "fail"

    if expected_outcome == "found_any_true":
        if not any(r.get("found_any") for r in tool_results):
            return "fail"

    elif expected_outcome == "booking_confirmed":
        if not any(r.get("status") == "success" for r in tool_results):
            return "fail"

    elif expected_outcome == "booking_cancelled":
        if not any(
            r.get("status") == "success" and
            (r.get("booking") or {}).get("status") == "cancelled"
            for r in tool_results
        ):
            return "fail"

    elif expected_outcome == "report_text_present":
        if not any(r.get("report_text") for r in tool_results):
            return "fail"

    elif expected_outcome in ("invalid_request_past_date",
                              "invalid_request_party_too_large",
                              "invalid_request_outside_hours"):
        if not any(r.get("status") in ("invalid_request", "error") for r in tool_results):
            return "fail"

    elif expected_outcome == "asks_for_missing_fields":
        if "confirmation_requested" in event_types or any(
            r.get("status") == "success" for r in tool_results
        ):
            return "fail"

    elif expected_outcome == "fallback_with_handoff_offer":
        if not fallbacks_seen:
            return "fail"
        last_reply = replies[-1]["assistant"] if replies else ""
        if "team member" not in last_reply.lower() and "help" not in last_reply.lower():
            return "fail"

    elif expected_outcome == "cancellation_rejected_by_user":
        if any(
            r.get("status") == "success" and
            (r.get("booking") or {}).get("status") == "cancelled"
            for r in tool_results
        ):
            return "fail"

    elif expected_outcome == "second_booking_conflict_error":
        if not any(r.get("status") == "error" for r in tool_results):
            return "fail"

    elif expected_outcome == "second_cancel_already_cancelled_error":
        if not any(r.get("error_code") == "ALREADY_CANCELLED" for r in tool_results):
            return "fail"

    return "pass"


def print_progress(test_id, description, verdict):
    icon = "✓" if verdict == "pass" else "✗" if verdict == "fail" else "!"
    print(f"  [{icon}] {test_id}: {description[:55]:<55} → {verdict.upper()}")


def main():
    retry_failed = "--retry-failed" in sys.argv
    redo_all = "--redo-all" in sys.argv

    print(f"\n[eval_runner.py version: {EVAL_RUNNER_VERSION}]")
    print("(If you don't see this exact line, you're running a stale copy of this file.)\n")

    cases = load_test_cases()
    existing = {} if redo_all else load_existing_results()

    if existing:
        already_passed = sum(1 for r in existing.values() if r["verdict"] == "pass")
        print(f"Found existing results.json with {len(existing)} test(s) recorded "
              f"({already_passed} passing).")
        if redo_all:
            print("--redo-all: ignoring existing results, running all 20 fresh.\n")
        elif retry_failed:
            print("--retry-failed: will re-run anything not currently passing.\n")
        else:
            print("Default mode: skipping tests that already PASSED. "
                  "Use --retry-failed to also retry failures, or --redo-all to start over.\n")

    to_run = []
    for case in cases:
        prior = existing.get(case["test_id"])
        if prior is None:
            to_run.append(case)
        elif prior["verdict"] != "pass" and (retry_failed or redo_all):
            to_run.append(case)
        elif redo_all:
            to_run.append(case)
        # else: already passed and not forcing a redo — leave it alone.

    if not to_run:
        print("Nothing to run — every test already has a PASS result.")
        print(f"Run eval_metrics.py to see the final report. ({RESULTS_PATH})")
        return

    print(f"Running {len(to_run)} of {len(cases)} test conversation(s) this session...")
    print(f"Inter-test sleep: {INTER_TEST_SLEEP_SECONDS}s (rate-limit buffer)\n")

    results_by_id = dict(existing)

    for i, case in enumerate(to_run):
        print(f"[{i+1:02d}/{len(to_run)}] {case['test_id']} — {case['description']}")
        result = run_one_case(case)
        results_by_id[case["test_id"]] = result
        save_results(results_by_id)  # incremental save — never lose progress
        print_progress(case["test_id"], case["description"], result["verdict"])

        if result.get("daily_quota_hit"):
            print(f"\n{'!'*60}")
            print("DAILY QUOTA EXHAUSTED — stopping this run early.")
            print("Every further call would also fail until the quota resets")
            print("(roughly midnight Pacific Time). Progress so far has been")
            print("saved. Run this script again later/tomorrow to continue —")
            print("it will automatically pick up where it left off.")
            print(f"{'!'*60}\n")
            break

        if i < len(to_run) - 1:
            time.sleep(INTER_TEST_SLEEP_SECONDS)

    passed = sum(1 for r in results_by_id.values() if r["verdict"] == "pass")
    failed = sum(1 for r in results_by_id.values() if r["verdict"] == "fail")
    errors = sum(1 for r in results_by_id.values() if r["verdict"] == "error")
    total_recorded = len(results_by_id)

    print(f"\n{'='*60}")
    print(f"Overall progress: {total_recorded}/{len(cases)} test(s) recorded")
    print(f"Results: {passed} passed / {failed} failed / {errors} errors")
    print(f"Results written to: {RESULTS_PATH}")
    if total_recorded < len(cases):
        print(f"{len(cases) - total_recorded} test(s) remaining — run this script again to continue.")
    else:
        print("All 20 test cases have a recorded result. Run eval_metrics.py next.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
