import unittest

from agent.deterministic_router import classify, is_confirmation_response


class TestDeterministicRouter(unittest.TestCase):
    def test_classifies_booking_request(self):
        self.assertEqual(classify("I'd like to book a table for Friday"), "book_table")

    def test_classifies_cancel_request_over_book_keywords(self):
        # Contains "reservation" which could ambiguously match book-ish
        # language, but "cancel" must win.
        self.assertEqual(classify("please cancel my reservation"), "cancel_booking")

    def test_classifies_modify_request(self):
        self.assertEqual(classify("can I reschedule my booking to 8pm"), "modify_booking")

    def test_classifies_info_request(self):
        self.assertEqual(classify("what time do you open"), "info_request")
        self.assertEqual(classify("do you have vegan options"), "info_request")

    def test_cancellation_policy_question_is_info_request_not_cancel_booking(self):
        # Regression test: this was misclassified as "unsupported" during a
        # live smoke test because the fallback router's keyword list didn't
        # cover FAQ topics, even though faqs.json has this exact answer.
        self.assertEqual(classify("what's your cancellation policy"), "info_request")
        self.assertEqual(classify("what is your reservation policy"), "info_request")

    def test_unrelated_text_is_unsupported(self):
        self.assertEqual(classify("what's the capital of France"), "unsupported")

    def test_affirmative_confirmation(self):
        for phrase in ["yes", "Yes please", "yeah go for it", "confirm", "sounds good", "ok"]:
            self.assertTrue(is_confirmation_response(phrase), msg=phrase)

    def test_negative_confirmation(self):
        for phrase in ["no", "no thanks", "cancel that", "actually no", "wait"]:
            self.assertFalse(is_confirmation_response(phrase), msg=phrase)

    def test_ambiguous_confirmation_returns_none(self):
        for phrase in ["maybe later", "what time is it", "I'm not sure"]:
            self.assertIsNone(is_confirmation_response(phrase), msg=phrase)


if __name__ == "__main__":
    unittest.main()
