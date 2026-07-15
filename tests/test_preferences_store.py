import unittest
from pathlib import Path

from tools import db_helper
from tools.preferences_store import get_preferences, upsert_after_booking
from tests.test_helpers import make_temp_db


class TestPreferencesStore(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = db_helper.DB_PATH
        db_helper.DB_PATH = Path(make_temp_db())

    def tearDown(self):
        Path(db_helper.DB_PATH).unlink(missing_ok=True)
        db_helper.DB_PATH = self._orig_db_path

    def test_unknown_customer_returns_none(self):
        self.assertIsNone(get_preferences("Nobody Ever Booked"))

    def test_first_booking_creates_record_with_visit_count_one(self):
        upsert_after_booking("Amal Sayegh", "+961 1 111111", 2, "indoor", "2026-07-01")
        prefs = get_preferences("Amal Sayegh")
        self.assertIsNotNone(prefs)
        self.assertEqual(prefs["visit_count"], 1)
        self.assertEqual(prefs["last_party_size"], 2)
        self.assertEqual(prefs["last_location"], "indoor")

    def test_second_booking_increments_visit_count_and_updates_last_visit(self):
        upsert_after_booking("Amal Sayegh", "+961 1 111111", 2, "indoor", "2026-07-01")
        upsert_after_booking("Amal Sayegh", "+961 1 111111", 5, "terrace", "2026-08-15")
        prefs = get_preferences("Amal Sayegh")
        self.assertEqual(prefs["visit_count"], 2)
        self.assertEqual(prefs["last_party_size"], 5)
        self.assertEqual(prefs["last_location"], "terrace")
        self.assertEqual(prefs["last_booking_date"], "2026-08-15")

    def test_missing_phone_on_later_booking_does_not_erase_known_phone(self):
        upsert_after_booking("Amal Sayegh", "+961 1 111111", 2, "indoor", "2026-07-01")
        upsert_after_booking("Amal Sayegh", None, 3, "indoor", "2026-09-01")
        prefs = get_preferences("Amal Sayegh")
        self.assertEqual(prefs["phone"], "+961 1 111111")  # not overwritten with None

    def test_empty_customer_name_is_a_no_op_not_an_error(self):
        upsert_after_booking("", "+961 1 111111", 2, "indoor", "2026-07-01")
        self.assertIsNone(get_preferences(""))


if __name__ == "__main__":
    unittest.main()
