import unittest

from agent.state import compute_missing_fields, initial_state


class TestState(unittest.TestCase):
    def test_initial_state_defaults(self):
        state = initial_state()
        self.assertEqual(state["messages"], [])
        self.assertIsNone(state["current_intent"])
        self.assertEqual(state["collected_fields"], {})
        self.assertFalse(state["pending_confirmation"])
        self.assertEqual(state["turn_iteration_count"], 0)

    def test_missing_fields_for_book_table(self):
        missing = compute_missing_fields("book_table", {"customer_name": "Sam"})
        self.assertIn("party_size", missing)
        self.assertIn("booking_date", missing)
        self.assertIn("booking_time", missing)
        self.assertNotIn("customer_name", missing)

    def test_no_missing_fields_when_all_present(self):
        fields = {"customer_name": "Sam", "party_size": 2, "booking_date": "2026-07-01", "booking_time": "19:00"}
        self.assertEqual(compute_missing_fields("book_table", fields), [])

    def test_info_request_never_has_missing_fields(self):
        self.assertEqual(compute_missing_fields("info_request", {}), [])

    def test_cancel_booking_requires_only_booking_id(self):
        self.assertEqual(compute_missing_fields("cancel_booking", {"booking_id": 5}), [])
        self.assertEqual(compute_missing_fields("cancel_booking", {}), ["booking_id"])


if __name__ == "__main__":
    unittest.main()
