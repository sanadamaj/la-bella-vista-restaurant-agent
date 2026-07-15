"""
llm_client.py - The only file that talks to the LLM provider.

Switched from Google Gemini to Anthropic Claude (June 2026) due to
Google's free-tier daily quota being too restrictive for a 20-conversation
evaluation suite (~20 requests/day cap vs ~70-100 calls needed).

Anthropic Tier 1 ($5 deposit, no daily cap) allows 50 RPM, which is
more than enough for both the evaluation suite and the live demo.

Everything else in the project (tools, workflow, Docker, eval runner)
is completely unchanged -b the LLM is swapped only in this one file.
"""

import json
import os
import re
import time
from typing import Any, Dict, Optional

try:
    import anthropic as anthropic_sdk
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    anthropic_sdk = None
    _ANTHROPIC_AVAILABLE = False

_client = None

VALID_INTENTS_FOR_PROMPT = [
    "info_request", "book_table", "modify_booking", "cancel_booking", "unsupported"
]

CLASSIFY_SYSTEM_PROMPT = f"""You are the intent router for a restaurant reservation assistant called
La Bella Vista. Given the recent conversation, output ONLY a JSON object (no markdown fences, no
commentary) with this exact shape:

{{
  "intent": one of {VALID_INTENTS_FOR_PROMPT},
  "confidence": a number from 0 to 1,
  "extracted_fields": {{
    "customer_name": string or null,
    "phone": string or null,
    "party_size": integer or null,
    "booking_date": "YYYY-MM-DD" string or null,
    "booking_time": "HH:MM" 24-hour string or null,
    "booking_id": integer or null,
    "table_id": integer or null,
    "special_requests": string or null,
    "query": string or null
  }}
}}

Rules:
- "query" is only for info_request: a short search phrase capturing what the user wants to know.
- "table_id" is only for a SPECIFIC table number the customer explicitly names (e.g. "table 3",
  "the table by the window if it's table 7"). Leave it null if they don't name one — the system
  will auto-assign a table in that case.
- Only fill a field if it is clearly stated. Leave everything else null.
- If the request doesn't match any supported intent, use "unsupported" with high confidence.
- confidence reflects how sure you are about the intent, not the fields."""

GENERATE_SYSTEM_PROMPT = """You are the customer-facing voice of La Bella Vista's reservation
assistant. Respond warmly and concisely (2-4 sentences unless listing menu items).
Base every factual claim strictly on the tool_result JSON provided. Never invent a dish, price,
policy, or booking detail that isn't in tool_result. Do not mention that you are an AI."""

_RETRY_DELAY_PATTERN = re.compile(r"retry[_-]delay[^\d]*(\d+)", re.IGNORECASE)
DEFAULT_RETRY_DELAY_SECONDS = 20


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate_limit" in text or "overloaded" in text or "quota" in text


def _is_daily_quota_error(exc: Exception) -> bool:
    """Anthropic doesn't have a daily cap like Google did — this check is
    kept for compatibility but will rarely trigger with Anthropic."""
    text = str(exc)
    return "PerDay" in text or "RequestsPerDay" in text


def _extract_retry_delay_seconds(exc: Exception) -> int:
    match = _RETRY_DELAY_PATTERN.search(str(exc))
    if match:
        return int(match.group(1)) + 1
    return DEFAULT_RETRY_DELAY_SECONDS


def _call_with_rate_limit_retry(fn):
    """Retries once on a per-minute rate limit; fails fast on daily quotas."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if not _is_rate_limit_error(exc):
            raise
        if _is_daily_quota_error(exc):
            raise  # daily cap — no point waiting under a minute
        delay = _extract_retry_delay_seconds(exc)
        time.sleep(delay)
        return fn()


def _get_client():
    global _client
    if not _ANTHROPIC_AVAILABLE:
        raise RuntimeError(
            "anthropic package is not installed. Run: pip install anthropic"
        )
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file (see .env.example)."
            )
        _client = anthropic_sdk.Anthropic(api_key=api_key)
    return _client


def _get_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")


def classify_intent(conversation_snippet: str) -> Dict[str, Any]:
    """Returns a dict with keys intent, confidence, extracted_fields.
    Never raises — any failure returns low-confidence None intent so the
    caller falls through to the deterministic router."""
    try:
        client = _get_client()
        model = _get_model()

        def _call():
            return client.messages.create(
                model=model,
                max_tokens=300,
                system=CLASSIFY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": conversation_snippet}],
            )

        response = _call_with_rate_limit_retry(_call)
        text = response.content[0].text.strip()
        # Strip markdown fences if the model adds them despite instructions
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        parsed.setdefault("extracted_fields", {})
        parsed.setdefault("confidence", 0.0)
        parsed.setdefault("intent", None)
        return parsed
    except Exception as exc:  # noqa: BLE001
        return {
            "intent": None,
            "confidence": 0.0,
            "extracted_fields": {},
            "error": str(exc),
        }


def generate_reply(
    conversation_snippet: str, tool_result: Optional[Dict[str, Any]]
) -> str:
    """Raises RuntimeError on failure — callers catch this and fall back
    to a deterministic message."""
    if not _ANTHROPIC_AVAILABLE:
        raise RuntimeError(
            "anthropic package is not installed. Run: pip install anthropic"
        )

    client = _get_client()
    model = _get_model()

    prompt = (
        f"Conversation so far:\n{conversation_snippet}\n\n"
        f"tool_result (ground every fact in this):\n{json.dumps(tool_result)}\n\n"
        "Write the assistant's next reply."
    )

    def _call():
        return client.messages.create(
            model=model,
            max_tokens=400,
            system=GENERATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

    response = _call_with_rate_limit_retry(_call)
    return response.content[0].text.strip()
