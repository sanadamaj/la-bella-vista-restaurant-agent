import unittest
from pathlib import Path

from agent import llm_client, nodes
from agent.state import initial_state
from tools import db_helper
from tests.test_helpers import future_date, make_temp_db


def _state_with_messages(*contents, **overrides):
    state = initial_state()
    state["messages"] = [{"role": "user", "content": c} for c in contents]
    state.update(overrides)
    return state


class _LLMPatchMixin:
    """Swaps llm_client's network-calling functions for canned responses
    for the duration of a test, and restores them afterward."""

    def setUp(self):
        super().setUp()
        self._orig_classify = llm_client.classify_intent
        self._orig_generate = llm_client.generate_reply

    def tearDown(self):
        llm_client.classify_intent = self._orig_classify
        llm_client.generate_reply = self._orig_generate
        super().tearDown()

    def set_classify_result(self, intent, confidence=0.9, extracted_fields=None):
        llm_client.classify_intent = lambda *_args, **_kwargs: {
            "intent": intent,
            "confidence": confidence,
            "extracted_fields": extracted_fields or {},
        }

    def set_classify_raises_or_unavailable(self):
        llm_client.classify_intent = lambda *_args, **_kwargs: {
            "intent": None, "confidence": 0.0, "extracted_fields": {}, "error": "simulated unavailable",
        }


class _TempDbMixin:
    def setUp(self):
        super().setUp()
        self._orig_db_path = db_helper.DB_PATH
        db_helper.DB_PATH = Path(make_temp_db())
        self.date = future_date(5)

    def tearDown(self):
        Path(db_helper.DB_PATH).unlink(missing_ok=True)
        db_helper.DB_PATH = self._orig_db_path
        super().tearDown()


class TestInterpretMessageNode(_LLMPatchMixin, _TempDbMixin, unittest.TestCase):
    def test_uses_llm_result_when_confidence_is_high(self):
        self.set_classify_result(
            "book_table", confidence=0.95,
            extracted_fields={"customer_name": "Maya", "party_size": 4},
        )
        state = _state_with_messages("book a table for 4")
        updates = nodes.interpret_message_node(state)

        self.assertEqual(updates["current_intent"], "book_table")
        self.assertEqual(updates["collected_fields"]["customer_name"], "Maya")
        self.assertEqual(updates["collected_fields"]["party_size"], 4)
        self.assertEqual(updates["low_confidence_streak"], 0)

    def test_falls_back_to_deterministic_router_on_low_confidence(self):
        self.set_classify_result("book_table", confidence=0.2)
        state = _state_with_messages("what time do you open")
        updates = nodes.interpret_message_node(state)

        # Low confidence -> fallback router used instead, which should
        # correctly read "what time do you open" as info_request even
        # though the LLM (mocked) suggested book_table.
        self.assertEqual(updates["current_intent"], "info_request")
        # The deterministic router successfully resolved a real intent —
        # this is not a "genuinely stuck" turn, so the streak stays at 0.
        # (Previously this asserted ==1, back when the streak counted any
        # LLM failure regardless of whether the fallback actually worked —
        # that broader definition was the root cause of bookings getting
        # wrongly killed after just two turns of LLM unavailability.)
        self.assertEqual(updates["low_confidence_streak"], 0)

    def test_falls_back_when_llm_unavailable(self):
        self.set_classify_raises_or_unavailable()
        state = _state_with_messages("cancel my reservation")
        updates = nodes.interpret_message_node(state)
        self.assertEqual(updates["current_intent"], "cancel_booking")
        self.assertEqual(updates["low_confidence_streak"], 0)  # resolved, not stuck

    def test_repeated_low_confidence_escalates_to_unsupported(self):
        self.set_classify_raises_or_unavailable()
        state = _state_with_messages("hmm")
        state["low_confidence_streak"] = 1  # already genuinely stuck once before this turn
        updates = nodes.interpret_message_node(state)
        self.assertEqual(updates["current_intent"], "unsupported")

    def test_bare_continuation_fragment_preserves_in_progress_intent(self):
        # Regression test for a real evaluation-suite failure (TC09): a
        # bare fragment like "My name is Gap Test" doesn't match any
        # deterministic-router keyword on its own. If the LLM happens to
        # be unavailable on exactly this turn, the OLD behavior wrongly
        # overwrote current_intent with "unsupported" and abandoned an
        # in-progress booking after a single bad turn.
        self.set_classify_raises_or_unavailable()
        state = _state_with_messages("My name is Gap Test")
        state["current_intent"] = "book_table"
        state["missing_fields"] = ["customer_name", "booking_date", "booking_time"]
        updates = nodes.interpret_message_node(state)
        self.assertEqual(updates["current_intent"], "book_table")
        self.assertEqual(updates["low_confidence_streak"], 0)  # successfully preserved, not stuck

    def test_explicit_new_intent_still_overrides_during_fallback(self):
        # The preservation fix must NOT prevent a genuine intent switch —
        # if the user explicitly says something the keyword router DOES
        # recognize, that should still take priority over the old intent.
        self.set_classify_raises_or_unavailable()
        state = _state_with_messages("actually, cancel my booking")
        state["current_intent"] = "book_table"
        state["missing_fields"] = ["booking_date"]
        updates = nodes.interpret_message_node(state)
        self.assertEqual(updates["current_intent"], "cancel_booking")

    def test_no_preservation_when_no_existing_action_intent(self):
        # If there's no in-progress action intent to fall back on (e.g.
        # current_intent is None, or it's info_request which has no
        # required fields to collect), a genuinely unmatched fragment
        # should still resolve to "unsupported" as before.
        self.set_classify_raises_or_unavailable()
        state = _state_with_messages("blah blah nonsense")
        state["current_intent"] = None
        state["missing_fields"] = []
        updates = nodes.interpret_message_node(state)
        self.assertEqual(updates["current_intent"], "unsupported")

    def test_string_party_size_is_coerced_to_int(self):
        self.set_classify_result("book_table", confidence=0.9, extracted_fields={"party_size": "4"})
        state = _state_with_messages("book for 4")
        updates = nodes.interpret_message_node(state)
        self.assertEqual(updates["collected_fields"]["party_size"], 4)
        self.assertIsInstance(updates["collected_fields"]["party_size"], int)

    def test_missing_fields_computed_for_partial_booking_info(self):
        self.set_classify_result("book_table", confidence=0.9, extracted_fields={"customer_name": "Sam"})
        state = _state_with_messages("book a table, my name is Sam")
        updates = nodes.interpret_message_node(state)
        self.assertIn("party_size", updates["missing_fields"])

    def test_skips_llm_entirely_when_awaiting_confirmation(self):
        # Regression test for the bug behind 6 evaluation-suite failures:
        # a "yes" reply during an active confirmation must never be sent
        # through intent reclassification at all, since a rate-limited or
        # uncertain LLM call (or the deterministic fallback, which has no
        # keywords for "yes") could overwrite current_intent with
        # "unsupported" and abandon an in-progress booking.
        call_count = {"n": 0}

        def _should_not_be_called(*_a, **_k):
            call_count["n"] += 1
            return {"intent": "unsupported", "confidence": 0.0, "extracted_fields": {}}

        llm_client.classify_intent = _should_not_be_called

        state = _state_with_messages("yes")
        state["confirmation_stage"] = "awaiting_response"
        state["current_intent"] = "book_table"  # what it was before this turn
        updates = nodes.interpret_message_node(state)

        self.assertEqual(call_count["n"], 0, "classify_intent must not be called during confirmation")
        self.assertEqual(updates, {})  # no state changes — current_intent stays "book_table" untouched


class TestRoutingFunctions(unittest.TestCase):
    def test_unsupported_routes_to_fallback(self):
        state = initial_state()
        state["current_intent"] = "unsupported"
        self.assertEqual(nodes.decide_next_step(state), "fallback")

    def test_iteration_cap_forces_fallback_regardless_of_intent(self):
        state = initial_state()
        state["current_intent"] = "info_request"
        state["turn_iteration_count"] = 99
        self.assertEqual(nodes.decide_next_step(state), "fallback")

    def test_info_request_goes_straight_to_execute(self):
        state = initial_state()
        state["current_intent"] = "info_request"
        self.assertEqual(nodes.decide_next_step(state), "execute_tools")

    def test_missing_fields_routes_to_ask_missing_info(self):
        state = initial_state()
        state["current_intent"] = "book_table"
        state["missing_fields"] = ["party_size"]
        self.assertEqual(nodes.decide_next_step(state), "ask_missing_info")

    def test_awaiting_confirmation_routes_to_resolve(self):
        state = initial_state()
        state["current_intent"] = "book_table"
        state["confirmation_stage"] = "awaiting_response"
        self.assertEqual(nodes.decide_next_step(state), "resolve_confirmation")

    def test_complete_action_with_no_confirmation_yet_requests_it(self):
        state = initial_state()
        state["current_intent"] = "cancel_booking"
        state["collected_fields"] = {"booking_id": 1}
        state["missing_fields"] = []
        self.assertEqual(nodes.decide_next_step(state), "request_confirmation")

    def test_awaiting_confirmation_wins_even_if_intent_is_unsupported(self):
        # Regression test: previously, if a rate-limited or misclassified
        # confirmation-turn reclassification set current_intent to
        # "unsupported", decide_next_step routed to fallback instead of
        # resolving the confirmation — silently abandoning an in-progress
        # booking right at the last step. confirmation_stage must win.
        state = initial_state()
        state["current_intent"] = "unsupported"
        state["confirmation_stage"] = "awaiting_response"
        self.assertEqual(nodes.decide_next_step(state), "resolve_confirmation")

    def test_decide_after_resolve_confirmation_branches(self):
        state = initial_state()
        state["confirmation_verdict"] = "confirmed"
        self.assertEqual(nodes.decide_after_resolve_confirmation(state), "execute")
        state["confirmation_verdict"] = "rejected"
        self.assertEqual(nodes.decide_after_resolve_confirmation(state), "rejected")
        state["confirmation_verdict"] = "ambiguous"
        self.assertEqual(nodes.decide_after_resolve_confirmation(state), "ambiguous")


class TestAskMissingInfoNode(unittest.TestCase):
    def test_single_missing_field_phrasing(self):
        state = initial_state()
        state["missing_fields"] = ["party_size"]
        updates = nodes.ask_missing_info_node(state)
        self.assertIn("how many guests", updates["messages"][0]["content"])

    def test_multiple_missing_fields_joined_naturally(self):
        state = initial_state()
        state["missing_fields"] = ["party_size", "booking_date"]
        updates = nodes.ask_missing_info_node(state)
        content = updates["messages"][0]["content"]
        self.assertIn("how many guests", content)
        self.assertIn("what date", content)
        self.assertIn("and", content)


class TestConfirmationFlow(_TempDbMixin, unittest.TestCase):
    def test_request_confirmation_for_valid_booking_returns_preview(self):
        state = initial_state()
        state["current_intent"] = "book_table"
        state["collected_fields"] = {
            "customer_name": "Lina", "party_size": 2, "booking_date": self.date, "booking_time": "19:00",
        }
        updates = nodes.request_confirmation_node(state)
        self.assertEqual(updates["confirmation_stage"], "awaiting_response")
        self.assertIn("confirm", updates["messages"][0]["content"].lower())

    def test_request_confirmation_skips_straight_to_error_when_invalid(self):
        state = initial_state()
        state["current_intent"] = "modify_booking"
        state["collected_fields"] = {"booking_id": 99999}  # doesn't exist
        updates = nodes.request_confirmation_node(state)
        self.assertIsNone(updates.get("confirmation_stage"))
        self.assertEqual(updates["last_tool_result"]["error_code"], "NOT_FOUND")

    def test_resolve_confirmation_yes(self):
        state = _state_with_messages("yes")
        updates = nodes.resolve_confirmation_node(state)
        self.assertEqual(updates["confirmation_verdict"], "confirmed")

    def test_resolve_confirmation_no(self):
        state = _state_with_messages("no, don't")
        updates = nodes.resolve_confirmation_node(state)
        self.assertEqual(updates["confirmation_verdict"], "rejected")

    def test_resolve_confirmation_ambiguous_does_not_touch_stage(self):
        state = _state_with_messages("hmm what do you mean")
        state["confirmation_stage"] = "awaiting_response"
        updates = nodes.resolve_confirmation_node(state)
        self.assertEqual(updates["confirmation_verdict"], "ambiguous")
        self.assertNotIn("confirmation_stage", updates)  # left untouched on purpose

    def test_respond_cancelled_clears_action_memory(self):
        state = initial_state()
        state["current_intent"] = "cancel_booking"
        state["collected_fields"] = {"booking_id": 1}
        updates = nodes.respond_cancelled_node(state)
        self.assertIsNone(updates["current_intent"])
        self.assertEqual(updates["collected_fields"], {})


class TestExecuteToolsNode(_TempDbMixin, unittest.TestCase):
    def test_info_request_returns_found_results(self):
        state = initial_state()
        state["current_intent"] = "info_request"
        state["collected_fields"] = {"query": "vegan"}
        updates = nodes.execute_tools_node(state)
        self.assertTrue(updates["last_tool_result"]["found_any"])
        self.assertIsNone(updates["current_intent"])  # action memory cleared

    def test_create_booking_success_includes_report_text(self):
        state = initial_state()
        state["current_intent"] = "book_table"
        state["collected_fields"] = {
            "customer_name": "Omar", "party_size": 2, "booking_date": self.date, "booking_time": "19:00",
        }
        updates = nodes.execute_tools_node(state)
        self.assertEqual(updates["last_tool_result"]["status"], "success")
        self.assertIn("Omar", updates["last_tool_result"]["report_text"])
        self.assertEqual(updates["turn_iteration_count"], 1)

    def test_cancel_nonexistent_booking_returns_error_without_crashing(self):
        state = initial_state()
        state["current_intent"] = "cancel_booking"
        state["collected_fields"] = {"booking_id": 99999}
        updates = nodes.execute_tools_node(state)
        self.assertEqual(updates["last_tool_result"]["status"], "error")
        self.assertEqual(updates["last_tool_result"]["error_code"], "NOT_FOUND")


class TestGenerateResponseNode(_LLMPatchMixin, unittest.TestCase):
    def test_uses_llm_reply_when_available(self):
        llm_client.generate_reply = lambda *_a, **_k: "Here's what I found, enjoy!"
        state = initial_state()
        state["last_tool_result"] = {"status": "success", "message": "ok"}
        updates = nodes.generate_response_node(state)
        self.assertEqual(updates["messages"][0]["content"], "Here's what I found, enjoy!")

    def test_falls_back_deterministically_when_llm_fails(self):
        def _raise(*_a, **_k):
            raise RuntimeError("simulated API failure")
        llm_client.generate_reply = _raise

        state = initial_state()
        state["last_tool_result"] = {"status": "error", "message": "no table available"}
        updates = nodes.generate_response_node(state)
        self.assertIn("no table available", updates["messages"][0]["content"])


class TestLongTermMemoryIntegration(_LLMPatchMixin, _TempDbMixin, unittest.TestCase):
    def test_looks_up_preferences_when_name_first_becomes_known(self):
        from tools.preferences_store import upsert_after_booking
        upsert_after_booking("Rana Saad", "+961 1 000111", 3, "outdoor", "2026-05-01")

        self.set_classify_result("book_table", confidence=0.9, extracted_fields={"customer_name": "Rana Saad"})
        state = _state_with_messages("book a table, I'm Rana Saad")
        updates = nodes.interpret_message_node(state)

        self.assertIsNotNone(updates["customer_preferences"])
        self.assertEqual(updates["customer_preferences"]["visit_count"], 1)

    def test_no_preferences_lookup_for_brand_new_customer(self):
        self.set_classify_result("book_table", confidence=0.9, extracted_fields={"customer_name": "Totally New Guest"})
        state = _state_with_messages("book a table, I'm a new guest")
        updates = nodes.interpret_message_node(state)
        self.assertIsNone(updates["customer_preferences"])

    def test_does_not_relookup_once_name_already_known_this_session(self):
        self.set_classify_result("info_request", confidence=0.9, extracted_fields={})
        state = _state_with_messages("what's on the menu")
        state["user_name"] = "Already Known Guest"
        state["customer_preferences"] = {"visit_count": 5}
        updates = nodes.interpret_message_node(state)
        # Should not have overwritten/re-queried — stays whatever it was.
        self.assertEqual(updates["customer_preferences"], {"visit_count": 5})

    def test_ask_missing_info_includes_suggestion_from_preferences(self):
        state = initial_state()
        state["missing_fields"] = ["party_size"]
        state["customer_preferences"] = {"last_party_size": 4}
        updates = nodes.ask_missing_info_node(state)
        self.assertIn("4", updates["messages"][0]["content"])

    def test_ask_missing_info_no_suggestion_without_preferences(self):
        state = initial_state()
        state["missing_fields"] = ["party_size"]
        state["customer_preferences"] = None
        updates = nodes.ask_missing_info_node(state)
        self.assertNotIn("Last time", updates["messages"][0]["content"])

    def test_successful_booking_persists_preferences_for_next_time(self):
        from tools.preferences_store import get_preferences
        state = initial_state()
        state["current_intent"] = "book_table"
        state["collected_fields"] = {
            "customer_name": "Fresh Guest", "party_size": 3, "booking_date": self.date, "booking_time": "19:00",
        }
        nodes.execute_tools_node(state)
        prefs = get_preferences("Fresh Guest")
        self.assertIsNotNone(prefs)
        self.assertEqual(prefs["visit_count"], 1)
        self.assertEqual(prefs["last_party_size"], 3)


class TestFallbackNode(unittest.TestCase):
    def test_fallback_message_and_state_reset(self):
        state = initial_state()
        state["current_intent"] = "unsupported"
        state["low_confidence_streak"] = 2
        updates = nodes.fallback_node(state)
        self.assertIn("team member", updates["messages"][0]["content"])
        self.assertEqual(updates["low_confidence_streak"], 0)
        self.assertIsNone(updates["current_intent"])


if __name__ == "__main__":
    unittest.main()
