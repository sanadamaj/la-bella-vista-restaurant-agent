"""
graph.py — Assembles agent/nodes.py into an actual LangGraph StateGraph.

IMPORTANT — read before relying on this file:
This is the one file in the whole project that could NOT be executed
during development, because this build environment has no network access
to `pip install langgraph`. Every node function it calls (agent/nodes.py)
is independently unit-tested with a mocked LLM and passes; what's untested
is LangGraph's own state-merging and checkpointing behavior matching what's
written here. Run `python3 scripts/manual_smoke_test.py` the moment you
have langgraph + google-generativeai installed and a real API key, BEFORE
building anything in Phase 4/5 on top of this — if there's an API mismatch
with your installed LangGraph version, you want to catch it here, not three
phases later.

No business logic lives in this file — every decision is delegated to
agent/nodes.py so that logic stays testable without LangGraph installed.
"""

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent import nodes
from agent.state import initial_state


class GraphState(TypedDict):
    # "messages" is the one field every node appends to rather than
    # replaces, so it needs the operator.add reducer; every other field
    # uses LangGraph's default "last write wins" behavior, which is what
    # we want since exactly one node updates each of them per turn.
    messages: Annotated[List[Dict[str, str]], operator.add]
    user_name: Optional[str]
    current_intent: Optional[str]
    collected_fields: Dict[str, Any]
    missing_fields: List[str]
    pending_confirmation: bool
    confirmation_stage: Optional[str]
    confirmation_verdict: Optional[str]
    last_confirmation_prompt: Optional[str]
    last_tool_result: Optional[Dict[str, Any]]
    workflow_state: str
    last_booking_id: Optional[int]
    customer_preferences: Optional[Dict[str, Any]]
    turn_iteration_count: int
    low_confidence_streak: int


def build_graph():
    builder = StateGraph(GraphState)

    builder.add_node("interpret_message", nodes.interpret_message_node)
    builder.add_node("ask_missing_info", nodes.ask_missing_info_node)
    builder.add_node("request_confirmation", nodes.request_confirmation_node)
    builder.add_node("resolve_confirmation", nodes.resolve_confirmation_node)
    builder.add_node("reask_confirmation", nodes.reask_confirmation_node)
    builder.add_node("respond_cancelled", nodes.respond_cancelled_node)
    builder.add_node("execute_tools", nodes.execute_tools_node)
    builder.add_node("generate_response", nodes.generate_response_node)
    builder.add_node("fallback", nodes.fallback_node)

    builder.add_edge(START, "interpret_message")

    builder.add_conditional_edges(
        "interpret_message",
        nodes.decide_next_step,
        {
            "fallback": "fallback",
            "execute_tools": "execute_tools",
            "ask_missing_info": "ask_missing_info",
            "request_confirmation": "request_confirmation",
            "resolve_confirmation": "resolve_confirmation",
        },
    )

    builder.add_edge("ask_missing_info", END)

    builder.add_conditional_edges(
        "request_confirmation",
        nodes.decide_after_request_confirmation,
        {"await_response": END, "respond": "generate_response"},
    )

    builder.add_conditional_edges(
        "resolve_confirmation",
        nodes.decide_after_resolve_confirmation,
        {"execute": "execute_tools", "rejected": "respond_cancelled", "ambiguous": "reask_confirmation"},
    )

    builder.add_edge("reask_confirmation", END)
    builder.add_edge("respond_cancelled", END)
    builder.add_edge("execute_tools", "generate_response")
    builder.add_edge("generate_response", END)
    builder.add_edge("fallback", END)

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def run_turn(session_id: str, user_message: str) -> str:
    """Sends one user message through the graph for the given session and
    returns the assistant's reply text. `session_id` is LangGraph's
    `thread_id` — short-term/working memory persists per session_id for
    the life of the process via MemorySaver (in-memory only; see
    PHASE3_NOTES.md on what Phase 4's long-term memory bonus would add)."""
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}

    snapshot = graph.get_state(config)
    is_first_turn = snapshot is None or not snapshot.values

    if is_first_turn:
        input_state = initial_state()
        input_state["messages"] = [{"role": "user", "content": user_message}]
    else:
        input_state = {"messages": [{"role": "user", "content": user_message}]}

    result = graph.invoke(input_state, config=config)
    assistant_messages = [m for m in result["messages"] if m["role"] == "assistant"]
    return assistant_messages[-1]["content"] if assistant_messages else "(no reply generated)"


def get_memory_snapshot(session_id: str) -> Optional[Dict[str, Any]]:
    """Returns the current short-term/working memory for a session in a
    flat, human-readable dict — meant for the live demo, Q&A, and the
    Phase 7 evaluation traces, not for the agent's own logic. Returns None
    if this session has had no turns yet."""
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        return None

    values = snapshot.values
    return {
        "user_name": values.get("user_name"),
        "turn_count": len(values.get("messages", [])),
        "current_intent": values.get("current_intent"),
        "collected_fields": values.get("collected_fields"),
        "missing_fields": values.get("missing_fields"),
        "confirmation_stage": values.get("confirmation_stage"),
        "workflow_state": values.get("workflow_state"),
        "last_tool_result_status": (values.get("last_tool_result") or {}).get("status"),
        "last_booking_id": values.get("last_booking_id"),
        "low_confidence_streak": values.get("low_confidence_streak"),
        "turn_iteration_count": values.get("turn_iteration_count"),
        "customer_preferences": values.get("customer_preferences"),
    }
