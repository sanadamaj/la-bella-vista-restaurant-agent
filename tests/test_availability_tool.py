import unittest
from pathlib import Path

from tools import db_helper
from tools.availability_tool import CheckAvailabilityInput, check_availability
from tests.test_helpers import make_temp_db, future_date


class TestAvailabilityTool(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db_helper.DB_PATH
        db_helper.DB_PATH = Path(make_temp_db())
        self.date = future_date(5)

    def tearDown(self):
        Path(db_helper.DB_PATH).unlink(missing_ok=True)
        db_helper.DB_PATH = self._original_db_path

    def test_available_when_no_conflicting_bookings(self):
        result = check_availability(
            CheckAvailabilityInput(booking_date=self.date, booking_time="19:00", party_size=2)
        )
        self.assertEqual(result.status, "available")
        self.assertTrue(len(result.available_tables) > 0)
        # Best-fit: smallest capacity that still fits should be first.
        self.assertLessEqual(result.available_tables[0].capacity, result.available_tables[-1].capacity)

    def test_invalid_time_in_the_gap_between_lunch_and_dinner(self):
        result = check_availability(
            CheckAvailabilityInput(booking_date=self.date, booking_time="16:30", party_size=2)
        )
        self.assertEqual(result.status, "invalid_request")
        self.assertIn("service hours", result.reason)

    def test_party_size_exceeds_max_capacity(self):
        result = check_availability(
            CheckAvailabilityInput(booking_date=self.date, booking_time="19:00", party_size=25)
        )
        self.assertEqual(result.status, "invalid_request")

    def test_malformed_date_is_invalid_request(self):
        result = check_availability(
            CheckAvailabilityInput(booking_date="2026/06/20", booking_time="19:00", party_size=2)
        )
        self.assertEqual(result.status, "invalid_request")

    def test_past_date_is_invalid_request(self):
        result = check_availability(
            CheckAvailabilityInput(booking_date="2020-01-01", booking_time="19:00", party_size=2)
        )
        self.assertEqual(result.status, "invalid_request")
        self.assertIn("past", result.reason)

    def test_terrace_filter_only_returns_terrace_tables(self):
        result = check_availability(
            CheckAvailabilityInput(
                booking_date=self.date, booking_time="19:00", party_size=4, location_preference="terrace"
            )
        )
        self.assertEqual(result.status, "available")
        self.assertTrue(all(t.location == "terrace" for t in result.available_tables))

    def test_accessible_required_excludes_inaccessible_tables(self):
        result = check_availability(
            CheckAvailabilityInput(
                booking_date=self.date, booking_time="19:00", party_size=2, accessible_required=True
            )
        )
        self.assertEqual(result.status, "available")
        self.assertTrue(all(t.accessible for t in result.available_tables))

    def test_overlapping_booking_blocks_only_the_occupied_table(self):
        # Party of 9 only fits table 12 (capacity 10) given the seeded
        # tables.json, so this isolates the overlap logic to one table.
        db_helper.insert_booking(
            customer_name="Existing Guest", phone=None, party_size=9,
            booking_date=self.date, booking_time="19:00", table_id=12, special_requests=None,
        )

        # Same slot: should now be unavailable.
        same_slot = check_availability(
            CheckAvailabilityInput(booking_date=self.date, booking_time="19:00", party_size=9)
        )
        self.assertEqual(same_slot.status, "unavailable")

        # A later, non-overlapping slot should free up again (90-minute
        # slot ends at 20:30, so 20:30 itself does not overlap).
        later_slot = check_availability(
            CheckAvailabilityInput(booking_date=self.date, booking_time="20:30", party_size=9)
        )
        self.assertEqual(later_slot.status, "available")

    def test_unavailable_slot_suggests_a_free_alternative_time(self):
        db_helper.insert_booking(
            customer_name="Existing Guest", phone=None, party_size=9,
            booking_date=self.date, booking_time="19:00", table_id=12, special_requests=None,
        )
        result = check_availability(
            CheckAvailabilityInput(booking_date=self.date, booking_time="19:00", party_size=9)
        )
        self.assertEqual(result.status, "unavailable")
        self.assertIn("20:30", result.suggested_alternative_times)


if __name__ == "__main__":
    unittest.main()
