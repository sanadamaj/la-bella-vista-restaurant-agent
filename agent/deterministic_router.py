"""
deterministic_router.py - Rule-based fallback logic with zero dependency
on the LLM. Used in two places:

1. As the routing fallback when the Gemini intent classifier is
   unavailable or returns low confidence (the project explicitly requires
   "a deterministic fallback when routing confidence is insufficient").
2. As the ONLY way a yes/no confirmation reply is interpreted. This is
   deliberate, not a shortcut: whether a sensitive, state-changing action
   actually executes should never depend on a probabilistic model
   correctly parsing "yeah go for it" - a fixed keyword match is safer and
   fully auditable.
"""

import re
from typing import Optional

INTENT_KEYWORDS = {
    "cancel_booking": ["cancel my", "cancel reservation", "cancel booking", "cancel the booking"],
    "modify_booking": ["change my", "reschedule", "move my reservation", "modify my booking", "update my booking"],
    "book_table": ["book a table", "make a reservation", "reserve a table", "table for", "book for", "i want to book"],
    "info_request": [
        "menu", "hours", "open", "close", "parking", "allergen", "vegan", "vegetarian",
        "gluten", "dress code", "wifi", "price", "cost", "dish", "kids menu", "pets", "pet",
        "wheelchair", "accessible", "accessibility", "payment", "credit card", "delivery",
        "takeout", "take-out", "private event", "location", "address", "where are you",
        "language", "cancellation", "cancellation policy", "reservation policy", "policy",
        "policies",
    ],
}

AFFIRMATIVE_PATTERNS = [
    r"^\s*yes\b", r"^\s*yep\b", r"^\s*yeah\b", r"^\s*confirm\b", r"^\s*sounds good\b",
    r"^\s*go ahead\b", r"^\s*that'?s (right|correct)\b", r"^\s*ok(ay)?\b", r"^\s*sure\b", r"^\s*do it\b",
]

NEGATIVE_PATTERNS = [
    r"^\s*no\b", r"^\s*nope\b", r"^\s*cancel that\b", r"^\s*don'?t\b", r"^\s*nevermind\b",
    r"^\s*never mind\b", r"^\s*wait\b", r"^\s*stop\b", r"^\s*that'?s wrong\b", r"^\s*actually no\b",
]


def classify(text: str) -> str:
    """Keyword-based intent fallback. Order matters: cancel/modify are
    checked before book_table since 'reservation' and 'booking' appear in
    both, and the more specific phrasing should win."""
    lowered = text.lower()
    for intent in ("cancel_booking", "modify_booking", "book_table", "info_request"):
        if any(kw in lowered for kw in INTENT_KEYWORDS[intent]):
            return intent
    return "unsupported"


def is_confirmation_response(text: str) -> Optional[bool]:
    """Returns True for a clear yes, False for a clear no, None if the
    reply doesn't match either pattern (caller should re-ask rather than
    guess)."""
    lowered = text.strip().lower()
    if any(re.search(p, lowered) for p in NEGATIVE_PATTERNS):
        return False
    if any(re.search(p, lowered) for p in AFFIRMATIVE_PATTERNS):
        return True
    return None
