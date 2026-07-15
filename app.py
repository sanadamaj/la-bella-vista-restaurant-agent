"""
app.py — Gradio web interface for La Bella Vista's reservation assistant.

This is intentionally a thin wrapper: every actual decision (routing,
tool calls, confirmation gates, fallback) already lives in agent/graph.py
and agent/nodes.py and is unit-tested there. This file's only job is
turning that into a usable chat UI — it shouldn't contain new business
logic that isn't already tested elsewhere.

Run locally with: python app.py
Inside Docker, this is the container's entrypoint (see Dockerfile).
"""

import os
import traceback
import uuid

import gradio as gr
from dotenv import load_dotenv

# Must run before importing anything from agent/tools — db_helper.py and
# data_loader.py read their path environment variables at import time,
# not lazily, so .env has to be loaded first.
load_dotenv()

from agent.graph import get_memory_snapshot, run_turn  # noqa: E402
from agent import logging_utils  # noqa: E402

RESTAURANT_NAME = "La Bella Vista"

WELCOME_MESSAGE = (
    f"Welcome to {RESTAURANT_NAME}! I can answer questions about our menu, hours, "
    "and policies, or help you book, change, or cancel a table reservation. "
    "How can I help?"
)


def new_session_id() -> str:
    return str(uuid.uuid4())


def on_submit(message: str, history: list, session_id: str):
    if not message or not message.strip():
        # Nothing to send — leave the chat and textbox exactly as they are.
        return gr.update(), history, session_id, gr.update()

    try:
        reply = run_turn(session_id, message)
    except Exception as exc:  # noqa: BLE001 — the UI must never crash on the customer
        logging_utils.log_event(
            "error",
            {"where": "on_submit", "exception": repr(exc), "traceback": traceback.format_exc()},
            session_id=session_id,
        )
        reply = (
            "Sorry, I'm having trouble responding right now. Please try again "
            "in a moment, or let a team member know if this keeps happening."
        )

    updated_history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]

    try:
        snapshot = get_memory_snapshot(session_id)
    except Exception:  # noqa: BLE001 — debug panel failing must never break the chat itself
        snapshot = {"error": "Could not load working memory snapshot."}

    return "", updated_history, session_id, snapshot


def on_new_conversation():
    fresh_id = new_session_id()
    fresh_history = [{"role": "assistant", "content": WELCOME_MESSAGE}]
    return fresh_history, fresh_id, None


def toggle_debug_panel(show: bool):
    return gr.update(visible=show)


def build_app() -> gr.Blocks:
    with gr.Blocks(title=f"{RESTAURANT_NAME} — Reservations") as demo:
        gr.Markdown(
            f"# {RESTAURANT_NAME} — Reservations Assistant\n"
            "Ask about the menu, hours, and policies, or book, change, or cancel a table."
        )

        session_id_state = gr.State(new_session_id)

        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    type="messages",
                    height=520,
                    label="Chat",
                    value=[{"role": "assistant", "content": WELCOME_MESSAGE}],
                )
                msg_box = gr.Textbox(
                    placeholder="Type your message and press Enter...",
                    show_label=False,
                    autofocus=True,
                )
                with gr.Row():
                    send_btn = gr.Button("Send", variant="primary")
                    new_conv_btn = gr.Button("New conversation")

            with gr.Column(scale=1):
                gr.Markdown("**For graders / demo:**")
                show_debug = gr.Checkbox(label="Show working memory (debug)", value=False)
                debug_panel = gr.JSON(label="Live working memory snapshot", visible=False)

        show_debug.change(toggle_debug_panel, inputs=show_debug, outputs=debug_panel)

        msg_box.submit(
            on_submit,
            inputs=[msg_box, chatbot, session_id_state],
            outputs=[msg_box, chatbot, session_id_state, debug_panel],
        )
        send_btn.click(
            on_submit,
            inputs=[msg_box, chatbot, session_id_state],
            outputs=[msg_box, chatbot, session_id_state, debug_panel],
        )
        new_conv_btn.click(
            on_new_conversation,
            outputs=[chatbot, session_id_state, debug_panel],
        )

    return demo


if __name__ == "__main__":
    app = build_app()
    port = int(os.environ.get("APP_PORT", 7860))
    # server_name="0.0.0.0" is required once this runs inside Docker — binding
    # to localhost-only would make it unreachable from outside the container.
    app.queue().launch(server_name="0.0.0.0", server_port=port)
