import unittest
from unittest.mock import patch

from agent.llm_client import (
    _call_with_rate_limit_retry,
    _extract_retry_delay_seconds,
    _is_daily_quota_error,
    _is_rate_limit_error,
)


class TestDailyQuotaDetection(unittest.TestCase):
    def test_detects_daily_quota_signature(self):
        self.assertTrue(_is_daily_quota_error(Exception(
            "GenerateRequestsPerDayPerProjectPerModel exceeded"
        )))

    def test_anthropic_rate_limit_not_flagged_as_daily(self):
        # Anthropic returns overloaded/rate_limit errors, not daily quotas
        self.assertFalse(_is_daily_quota_error(Exception("rate_limit_error")))
        self.assertFalse(_is_daily_quota_error(Exception("overloaded_error")))


class TestRateLimitDetection(unittest.TestCase):
    def test_detects_429(self):
        self.assertTrue(_is_rate_limit_error(Exception("429 rate limit")))

    def test_detects_rate_limit_keyword(self):
        self.assertTrue(_is_rate_limit_error(Exception("rate_limit_error")))

    def test_detects_overloaded(self):
        self.assertTrue(_is_rate_limit_error(Exception("overloaded_error")))

    def test_non_rate_limit_not_flagged(self):
        self.assertFalse(_is_rate_limit_error(Exception("invalid API key")))


class TestRetryDelayExtraction(unittest.TestCase):
    def test_extracts_delay_from_error(self):
        self.assertEqual(_extract_retry_delay_seconds(Exception("retry-delay 22 seconds")), 23)

    def test_falls_back_to_default(self):
        self.assertEqual(_extract_retry_delay_seconds(Exception("no delay info")), 20)


class TestCallWithRateLimitRetry(unittest.TestCase):
    @patch("agent.llm_client.time.sleep", return_value=None)
    def test_retries_once_on_rate_limit_then_succeeds(self, mock_sleep):
        calls = {"count": 0}

        def flaky():
            calls["count"] += 1
            if calls["count"] == 1:
                raise Exception("429 rate limit exceeded, retry-delay 5 seconds")
            return "success"

        result = _call_with_rate_limit_retry(flaky)
        self.assertEqual(result, "success")
        self.assertEqual(calls["count"], 2)
        mock_sleep.assert_called_once()

    def test_non_rate_limit_error_propagates_immediately(self):
        calls = {"count": 0}

        def always_fails():
            calls["count"] += 1
            raise ValueError("invalid key")

        with self.assertRaises(ValueError):
            _call_with_rate_limit_retry(always_fails)
        self.assertEqual(calls["count"], 1)

    @patch("agent.llm_client.time.sleep", return_value=None)
    def test_daily_quota_fails_fast_no_retry(self, mock_sleep):
        calls = {"count": 0}

        def always_daily_quota():
            calls["count"] += 1
            raise Exception("GenerateRequestsPerDayPerProjectPerModel exceeded")

        with self.assertRaises(Exception):
            _call_with_rate_limit_retry(always_daily_quota)
        self.assertEqual(calls["count"], 1)
        mock_sleep.assert_not_called()

    @patch("agent.llm_client.time.sleep", return_value=None)
    def test_second_failure_after_retry_propagates(self, mock_sleep):
        def always_rate_limited():
            raise Exception("429 rate limited")

        with self.assertRaises(Exception):
            _call_with_rate_limit_retry(always_rate_limited)
