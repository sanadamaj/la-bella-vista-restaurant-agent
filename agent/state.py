"""
state.py — Defines the shape of conversation/working state the graph
passes between nodes, and the deterministic rules for what each intent
requires before it's actionable.

This is intentionally plain Python (TypedDict + dict factory), not a
LangGraph-specific construct, so it can be created and inspected in tests
without LangGraph installed. graph.py is the only file that wires this
into an actual StateGraph.
"""

from typing import Any, Dict, List, Optional, TypedDict

VALID_INTENTS = {"info_request", "book_table", "modify_booking", "cancel_booking", "unsupported"}

ACTION_INTENTS = {"book_table", "modify_booking", "cancel_booking"}

# What each intent needs collected before a tool can act on it. Anything
# missing routes to the ask_missing_info node instead of executing.
REQUIRED_FIELDS: Dict[str, List[str]] = {
    "book_table": ["customer_name", "party_size", "booking_date", "booking_time"],
    "modify_booking": ["booking_id"],
    "cancel_booking": ["booking_id"],
    "info_request": [],
    "unsupported": [],
}

# Human-readable prompt fragments used by the (deterministic, non-LLM)
# ask_missing_info node.
FIELD_QUESTIONS: Dict[str, str] = {
    "customer_name": "the name for the reservation",
    "party_size": "how many guests",
    "booking_date": "what date you'd like (YYYY-MM-DD)",
    "booking_time": "what time you'd like (24h HH:MM)",
    "booking_id": "the booking number (I can look it up if you give me the name and date instead)",
}

CONFIDENCE_THRESHOLD = 0.55
MAX_ITERATIONS_PER_TURN = 3
LOW_CONFIDENCE_STREAK_LIMIT = 2


def compute_missing_fields(intent: str, collected_fields: Dict[str, Any]) -> List[str]:
    required = REQUIRED_FIELDS.get(intent, [])
    return [f for f in required if not collected_fields.get(f)]


class AgentState(TypedDict):
    # Short-term memory
    messages: List[Dict[str, str]]  # [{"role": "user"|"assistant", "content": str}]
    user_name: Optional[str]

    # Working memory (explicitly required by the proposal)
    current_intent: Optional[str]
    collected_fields: Dict[str, Any]
    missing_fields: List[str]
    pending_confirmation: bool
    confirmation_stage: Optional[str]  # None | "awaiting_response"
    confirmation_verdict: Optional[str]  # None | "confirmed" | "rejected" | "ambiguous"
    last_confirmation_prompt: Optional[str]
    last_tool_result: Optional[Dict[str, Any]]
    workflow_state: str  # idle | collecting_info | confirming | executing | responding | fallback

    # The most recent booking_id this session successfully created, modified,
    # or attempted to cancel. Lets a bare follow-up like "cancel my booking
    # again" resolve without the customer re-stating the number — see
    # interpret_message_node / execute_tools_node in nodes.py.
    last_booking_id: Optional[int]

    # Long-term memory (bonus) — looked up by customer_name once known,
    # None if this customer has no recorded visit history.
    customer_preferences: Optional[Dict[str, Any]]

    # Control / observability
    turn_iteration_count: int
    low_confidence_streak: int


def initial_state() -> AgentState:
    return AgentState(
        messages=[],
        user_name=None,
        current_intent=None,
        collected_fields={},
        missing_fields=[],
        pending_confirmation=False,
        confirmation_stage=None,
        confirmation_verdict=None,
        last_confirmation_prompt=None,
        last_tool_result=None,
        workflow_state="idle",
        last_booking_id=None,
        customer_preferences=None,
        turn_iteration_count=0,
        low_confidence_streak=0,
    )
