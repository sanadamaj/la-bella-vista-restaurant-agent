"""
nodes.py — All workflow node functions, written as plain Python
(state dict in, partial-update dict out) so they can be unit-tested
without LangGraph, a database connection beyond the test fixture, or a
real Gemini API key. graph.py wires these into an actual StateGraph and
adds nothing else — no business logic should ever live there.

Each node follows the LangGraph node convention: it receives the full
current state and returns only the keys it wants to change. Keys not
returned are left untouched by the graph's merge step.
"""

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from agent import deterministic_router, llm_client, logging_utils
from agent.state import (
    ACTION_INTENTS,
    CONFIDENCE_THRESHOLD,
    FIELD_QUESTIONS,
    LOW_CONFIDENCE_STREAK_LIMIT,
    MAX_ITERATIONS_PER_TURN,
    VALID_INTENTS,
    AgentState,
    compute_missing_fields,
)
from tools.availability_tool import CheckAvailabilityInput, check_availability
from tools.booking_tool import ManageBookingInput, manage_booking
from tools.data_loader import get_table_by_id
from tools.info_tool import GetRestaurantInfoInput, get_restaurant_info
from tools.preferences_store import get_preferences, upsert_after_booking
from tools.reporting_tool import GenerateSummaryReportInput, generate_summary_report

INTENT_TO_ACTION = {"book_table": "create", "modify_booking": "modify", "cancel_booking": "cancel"}


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _last_user_message(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if msg["role"] == "user":
            return msg["content"]
    return ""


def _format_conversation(state: AgentState, max_turns: int = 8) -> str:
    recent = state["messages"][-max_turns:]
    return "\n".join(f"{m['role']}: {m['content']}" for m in recent)


def _coerce_field_types(fields: Dict[str, Any]) -> Dict[str, Any]:
    """The LLM should return party_size/booking_id as JSON numbers already,
    but if it ever returns a numeral string, coerce rather than fail later
    deep inside a tool's validation."""
    coerced = dict(fields)
    for key in ("party_size", "booking_id", "table_id"):
        value = coerced.get(key)
        if isinstance(value, str) and value.strip().isdigit():
            coerced[key] = int(value.strip())
    return coerced


def _build_manage_booking_payload(intent: str, fields: Dict[str, Any], confirmed: bool) -> ManageBookingInput:
    return ManageBookingInput(
        action=INTENT_TO_ACTION[intent],
        confirmed=confirmed,
        booking_id=fields.get("booking_id"),
        customer_name=fields.get("customer_name"),
        phone=fields.get("phone"),
        party_size=fields.get("party_size"),
        booking_date=fields.get("booking_date"),
        booking_time=fields.get("booking_time"),
        table_id=fields.get("table_id"),
        special_requests=fields.get("special_requests"),
    )


def _clear_action_memory(updates: Dict[str, Any]) -> Dict[str, Any]:
    updates.update(
        current_intent=None,
        collected_fields={},
        missing_fields=[],
        confirmation_stage=None,
        confirmation_verdict=None,
    )
    return updates


# ---------------------------------------------------------------------------
# Node: interpret_message — classification + field extraction + merge
# ---------------------------------------------------------------------------

def interpret_message_node(state: AgentState) -> Dict[str, Any]:
    # If we're actively waiting on a yes/no confirmation reply, don't
    # reclassify intent at all. A "yes"/"confirm"/"no" reply isn't a new
    # request, and deterministic_router.classify() has no keywords for
    # confirmation words (that logic lives separately in
    # is_confirmation_response(), used later by resolve_confirmation_node).
    # Previously, if the LLM was rate-limited or simply uncertain on this
    # turn, classify() would fall through to "unsupported", overwrite
    # current_intent, and decide_next_step would route to fallback —
    # abandoning an in-progress booking or cancellation right at the last
    # step. Skipping reclassification here also saves an LLM call on every
    # confirmation turn, which matters given the free-tier daily quota.
    if state.get("confirmation_stage") == "awaiting_response":
        logging_utils.log_event(
            "intent_classified",
            {
                "intent": state.get("current_intent"),
                "confidence": 1.0,
                "used_fallback": False,
                "note": "skipped_reclassification_awaiting_confirmation",
            },
        )
        return {}

    last_message = _last_user_message(state)
    conversation_snippet = _format_conversation(state)

    llm_result = llm_client.classify_intent(conversation_snippet)
    intent = llm_result.get("intent")
    confidence = llm_result.get("confidence") or 0.0
    extracted_fields = llm_result.get("extracted_fields") or {}

    used_fallback = False
    if intent not in VALID_INTENTS or confidence < CONFIDENCE_THRESHOLD:
        used_fallback = True
        fallback_intent = deterministic_router.classify(last_message)
        extracted_fields = {}

        # A bare continuation fragment — just a name, just a date, just a
        # phone number — won't match any of the deterministic router's
        # keyword lists on its own; it only makes sense in the context of
        # an already-established intent. That's expected and NOT a sign
        # the request is unsupported. If we're already mid-collecting
        # fields for a real action intent, preserve it rather than
        # overwriting with "unsupported" just because this one fragment,
        # read in isolation, doesn't match a keyword. (A genuinely new,
        # explicit intent — e.g. "actually, cancel my booking" — still
        # correctly overrides, since the keyword router DOES catch that.)
        existing_intent = state.get("current_intent")
        existing_missing = state.get("missing_fields") or []
        if fallback_intent == "unsupported" and existing_intent in ACTION_INTENTS and existing_missing:
            intent = existing_intent
        else:
            intent = fallback_intent

        logging_utils.log_event(
            "fallback",
            {"reason": "low_confidence_or_llm_unavailable", "llm_result": llm_result, "fallback_intent": intent},
        )

    # Tracks consecutive turns where NOTHING could resolve an actionable
    # intent — not the LLM, not the keyword router, not the in-progress-
    # intent preservation above. This is deliberately narrower than "the
    # LLM call merely failed": once a daily API quota is exhausted, EVERY
    # subsequent turn would otherwise count as a failure and force a hard
    # "unsupported" after just two turns, even while the deterministic
    # fallback (with the preservation above) is handling things just fine.
    genuinely_stuck = used_fallback and intent == "unsupported"
    low_confidence_streak = state["low_confidence_streak"] + 1 if genuinely_stuck else 0
    if low_confidence_streak >= LOW_CONFIDENCE_STREAK_LIMIT:
        logging_utils.log_event("fallback", {"reason": "repeated_low_confidence_streak"})
        intent = "unsupported"

    logging_utils.log_event(
        "intent_classified",
        {"intent": intent, "confidence": confidence, "used_fallback": used_fallback},
    )

    new_collected = dict(state["collected_fields"])
    for key, value in _coerce_field_types(extracted_fields).items():
        if value not in (None, ""):
            new_collected[key] = value

    # "cancel my booking again" / "change my booking" don't restate a
    # booking number — fall back to the most recent booking this session
    # touched rather than making the customer look it up. An explicit
    # number given this turn (handled above) always takes precedence.
    if intent in ("modify_booking", "cancel_booking") and not new_collected.get("booking_id"):
        last_booking_id = state.get("last_booking_id")
        if last_booking_id:
            new_collected["booking_id"] = last_booking_id

    missing = compute_missing_fields(intent, new_collected)
    previously_known_name = state.get("user_name")
    new_user_name = previously_known_name or new_collected.get("customer_name")

    customer_preferences = state.get("customer_preferences")
    if new_user_name and not previously_known_name:
        # Name just became known this turn — look up visit history exactly
        # once rather than re-querying the DB on every subsequent turn.
        customer_preferences = get_preferences(new_user_name)
        if customer_preferences:
            logging_utils.log_event(
                "tool_result",
                {"stage": "long_term_memory_lookup", "customer_name": new_user_name, "found": True},
            )

    return {
        "current_intent": intent,
        "collected_fields": new_collected,
        "missing_fields": missing,
        "low_confidence_streak": low_confidence_streak,
        "user_name": new_user_name,
        "customer_preferences": customer_preferences,
        "workflow_state": (
            "fallback" if intent == "unsupported"
            else "collecting_info" if missing
            else "confirming" if intent in ACTION_INTENTS
            else "executing"
        ),
    }


# ---------------------------------------------------------------------------
# Pure routing functions (used by graph.py's conditional edges, and
# directly unit-testable without ever building a graph)
# ---------------------------------------------------------------------------

def decide_next_step(state: AgentState) -> str:
    if state["turn_iteration_count"] >= MAX_ITERATIONS_PER_TURN:
        return "fallback"
    # Check this BEFORE the unsupported/None intent check below. If we're
    # mid-confirmation, a yes/no reply must always resolve the
    # confirmation, regardless of whatever current_intent happens to be —
    # see interpret_message_node's early-return for the bug this guards
    # against.
    if state["confirmation_stage"] == "awaiting_response":
        return "resolve_confirmation"
    intent = state["current_intent"]
    if intent is None or intent == "unsupported":
        return "fallback"
    if intent == "info_request":
        return "execute_tools"
    if state["missing_fields"]:
        return "ask_missing_info"
    return "request_confirmation"


def decide_after_request_confirmation(state: AgentState) -> str:
    return "await_response" if state["confirmation_stage"] == "awaiting_response" else "respond"


def decide_after_resolve_confirmation(state: AgentState) -> str:
    verdict = state.get("confirmation_verdict")
    if verdict == "confirmed":
        return "execute"
    if verdict == "rejected":
        return "rejected"
    return "ambiguous"


# ---------------------------------------------------------------------------
# Node: ask_missing_info
# ---------------------------------------------------------------------------

def ask_missing_info_node(state: AgentState) -> Dict[str, Any]:
    phrases = [FIELD_QUESTIONS.get(f, f) for f in state["missing_fields"]]
    if len(phrases) == 1:
        question = f"Could you tell me {phrases[0]}?"
    else:
        question = "Could you tell me " + ", ".join(phrases[:-1]) + f", and {phrases[-1]}?"

    prefs = state.get("customer_preferences")
    if prefs and "party_size" in state["missing_fields"] and prefs.get("last_party_size"):
        question += (
            f" (Last time you booked for {prefs['last_party_size']} — let me know if "
            "it's the same this time, or a different number.)"
        )

    return {
        "messages": [{"role": "assistant", "content": question}],
        "workflow_state": "collecting_info",
    }


# ---------------------------------------------------------------------------
# Node: request_confirmation
# ---------------------------------------------------------------------------

def request_confirmation_node(state: AgentState) -> Dict[str, Any]:
    intent = state["current_intent"]
    payload = _build_manage_booking_payload(intent, state["collected_fields"], confirmed=False)
    result = manage_booking(payload)

    logging_utils.log_event("confirmation_requested", {"intent": intent, "status": result.status})

    if result.status == "pending_confirmation":
        return {
            "messages": [{"role": "assistant", "content": result.message}],
            "confirmation_stage": "awaiting_response",
            "last_confirmation_prompt": result.message,
            "workflow_state": "confirming",
        }

    # The preview itself failed (e.g. NOT_FOUND, NO_AVAILABILITY, CONFLICT,
    # ALREADY_CANCELLED) — nothing to confirm, skip straight to telling the
    # user what happened. Clear action memory the same way execute_tools_node
    # and respond_cancelled_node do: this turn is over, and the next user
    # message shouldn't inherit a stale current_intent/collected_fields from
    # a request that already resolved (previously this branch forgot to
    # clear it, which could corrupt the very next turn).
    result_dict = asdict(result)
    logging_utils.log_event("tool_result", {"intent": intent, "result": result_dict})
    updates = {
        "last_tool_result": result_dict,
        "confirmation_stage": None,
        "workflow_state": "responding",
    }
    return _clear_action_memory(updates)


# ---------------------------------------------------------------------------
# Node: resolve_confirmation
# ---------------------------------------------------------------------------

def resolve_confirmation_node(state: AgentState) -> Dict[str, Any]:
    last_message = _last_user_message(state)
    verdict = deterministic_router.is_confirmation_response(last_message)
    verdict_str = "confirmed" if verdict is True else "rejected" if verdict is False else "ambiguous"

    logging_utils.log_event("confirmation_resolved", {"verdict": verdict_str})

    if verdict_str == "ambiguous":
        return {"confirmation_verdict": "ambiguous"}

    return {"confirmation_verdict": verdict_str, "confirmation_stage": None}


def reask_confirmation_node(state: AgentState) -> Dict[str, Any]:
    prompt = state.get("last_confirmation_prompt") or "Should I go ahead with that?"
    return {
        "messages": [{"role": "assistant", "content": f"Sorry, just to double check — {prompt} Please reply yes or no."}],
        "confirmation_verdict": None,
    }


def respond_cancelled_node(state: AgentState) -> Dict[str, Any]:
    updates = {
        "messages": [{"role": "assistant", "content": "No problem, I won't go ahead with that. Anything else I can help with?"}],
        "last_tool_result": None,
        "workflow_state": "idle",
    }
    return _clear_action_memory(updates)


# ---------------------------------------------------------------------------
# Node: execute_tools — the only node that actually calls a domain tool
# for action intents (info_request also routes here directly)
# ---------------------------------------------------------------------------

def execute_tools_node(state: AgentState) -> Dict[str, Any]:
    intent = state["current_intent"]
    fields = state["collected_fields"]
    logging_utils.log_event("tool_call", {"intent": intent, "fields": fields})

    # Defined up front so the except branch below can safely fall back to
    # it without a NameError if manage_booking() raises before setting it.
    resolved_booking_id = fields.get("booking_id")

    try:
        if intent == "info_request":
            query = fields.get("query") or _last_user_message(state) or "general information"
            result = get_restaurant_info(GetRestaurantInfoInput(query=query))
            result_dict = asdict(result)

        elif intent in INTENT_TO_ACTION:
            payload = _build_manage_booking_payload(intent, fields, confirmed=True)
            booking_result = manage_booking(payload)
            result_dict = asdict(booking_result)

            # Remember this booking_id regardless of outcome: a successful
            # create gives us a brand-new id to remember, and a modify/cancel
            # (successful or not — e.g. ALREADY_CANCELLED) reuses the id the
            # customer already gave us. Either way, "my booking" on the next
            # turn should keep resolving to it.
            if booking_result.booking:
                resolved_booking_id = booking_result.booking.get("booking_id")

            if booking_result.status == "success" and booking_result.booking:
                report = generate_summary_report(
                    GenerateSummaryReportInput(booking_id=booking_result.booking["booking_id"])
                )
                result_dict["report_text"] = report.report_text

                if intent == "book_table":
                    try:
                        table = get_table_by_id(booking_result.booking["table_id"])
                        upsert_after_booking(
                            customer_name=booking_result.booking["customer_name"],
                            phone=booking_result.booking.get("phone"),
                            party_size=booking_result.booking["party_size"],
                            location=table["location"] if table else None,
                            booking_date=booking_result.booking["booking_date"],
                        )
                    except Exception as exc:  # noqa: BLE001 — bonus feature must never break a real booking
                        logging_utils.log_event(
                            "error", {"stage": "long_term_memory_write", "error": str(exc)}
                        )

        else:
            result_dict = {
                "status": "error",
                "message": "That request isn't something I'm able to act on.",
                "error_code": "UNSUPPORTED",
            }

    except Exception as exc:  # noqa: BLE001 — must degrade to a safe message, never crash the turn
        logging_utils.log_event("error", {"stage": "execute_tools", "intent": intent, "error": str(exc)})
        result_dict = {
            "status": "error",
            "message": "Something went wrong while processing that. Please try again.",
            "error_code": "INTERNAL_ERROR",
        }

    logging_utils.log_event("tool_result", {"intent": intent, "result": result_dict})

    updates = {
        "last_tool_result": result_dict,
        "turn_iteration_count": state["turn_iteration_count"] + 1,
        "workflow_state": "responding",
    }
    if intent in INTENT_TO_ACTION:
        updates["last_booking_id"] = resolved_booking_id or state.get("last_booking_id")
    return _clear_action_memory(updates)


# ---------------------------------------------------------------------------
# Node: generate_response
# ---------------------------------------------------------------------------

def _deterministic_reply_fallback(tool_result: Optional[Dict[str, Any]]) -> str:
    if not tool_result:
        return "I've made a note of that. Is there anything else I can help with?"
    status = tool_result.get("status")
    if status == "success":
        return tool_result.get("report_text") or tool_result.get("message") or "All set!"
    if status in ("not_found", "unavailable", "invalid_request", "error"):
        return tool_result.get("reason") or tool_result.get("message") or "I wasn't able to do that."
    if "found_any" in tool_result:
        if not tool_result.get("found_any"):
            return "I couldn't find anything matching that in our menu or FAQs. Could you rephrase?"
        return "Here's what I found — let me know if you'd like more detail."
    return "Here's what I found out."


def generate_response_node(state: AgentState) -> Dict[str, Any]:
    tool_result = state.get("last_tool_result")
    conversation_snippet = _format_conversation(state)
    try:
        reply = llm_client.generate_reply(conversation_snippet, tool_result)
    except Exception as exc:  # noqa: BLE001
        logging_utils.log_event("error", {"stage": "generate_response", "error": str(exc)})
        reply = _deterministic_reply_fallback(tool_result)

    return {
        "messages": [{"role": "assistant", "content": reply}],
        "workflow_state": "idle",
    }


# ---------------------------------------------------------------------------
# Node: fallback
# ---------------------------------------------------------------------------

def fallback_node(state: AgentState) -> Dict[str, Any]:
    logging_utils.log_event(
        "fallback",
        {"intent": state.get("current_intent"), "reason": "unsupported_or_iteration_cap_reached"},
    )
    message = (
        "I'm not able to help with that here — I can answer questions about our menu, hours, "
        "and policies, or help you book, change, or cancel a table reservation. Would you like "
        "me to connect you with a team member for anything else?"
    )
    updates = {
        "messages": [{"role": "assistant", "content": message}],
        "low_confidence_streak": 0,
        "turn_iteration_count": 0,
        "workflow_state": "fallback",
    }
    return _clear_action_memory(updates)
