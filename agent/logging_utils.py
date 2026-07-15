"""
logging_utils.py — Structured trace logging.

Every intent classification, tool call/result, error, and fallback event
gets written as one JSON line, both to stdout (so `docker logs` shows it
live) and to a log file (so the Phase 7 evaluation suite can analyze
traces after the fact). One JSON object per line keeps this greppable and
easy to load with `pandas.read_json(path, lines=True)` for the metrics in
the evaluation report.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

LOG_PATH = Path(os.environ.get("LOG_PATH", Path(__file__).resolve().parent.parent / "logs" / "agent_trace.jsonl"))

VALID_EVENT_TYPES = {
    "intent_classified",
    "tool_call",
    "tool_result",
    "error",
    "fallback",
    "confirmation_requested",
    "confirmation_resolved",
}


def log_event(event_type: str, payload: Dict[str, Any], session_id: str = "default") -> None:
    if event_type not in VALID_EVENT_TYPES:
        # Logging a malformed event type shouldn't crash the agent — log
        # it as an error about itself instead.
        payload = {"original_event_type": event_type, "original_payload": payload}
        event_type = "error"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "event_type": event_type,
        "payload": payload,
    }
    line = json.dumps(entry, default=str)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    print(line, file=sys.stdout, flush=True)
