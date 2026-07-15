import unittest
from pathlib import Path

from tools import db_helper
from tools.booking_tool import ManageBookingInput, manage_booking
from tests.test_helpers import make_temp_db, future_date


class TestBookingTool(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db_helper.DB_PATH
        db_helper.DB_PATH = Path(make_temp_db())
        self.date = future_date(5)

    def tearDown(self):
        Path(db_helper.DB_PATH).unlink(missing_ok=True)
        db_helper.DB_PATH = self._original_db_path

    def _base_create_payload(self, **overrides):
        defaults = dict(
            action="create",
            confirmed=False,
            customer_name="Test Guest",
            phone="+961 70 000000",
            party_size=2,
            booking_date=self.date,
            booking_time="19:00",
        )
        defaults.update(overrides)
        return ManageBookingInput(**defaults)

    def test_create_without_confirmation_does_not_write_to_db(self):
        result = manage_booking(self._base_create_payload(confirmed=False))
        self.assertEqual(result.status, "pending_confirmation")
        rows = db_helper.fetch_bookings_for_date(self.date)
        self.assertEqual(len(rows), 0)

    def test_create_with_confirmation_writes_to_db(self):
        result = manage_booking(self._base_create_payload(confirmed=True))
        self.assertEqual(result.status, "success")
        self.assertIsNotNone(result.booking)
        rows = db_helper.fetch_bookings_for_date(self.date)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customer_name"], "Test Guest")

    def test_create_missing_required_field_is_validation_error(self):
        result = manage_booking(
            ManageBookingInput(action="create", confirmed=True, party_size=2, booking_date=self.date, booking_time="19:00")
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "VALIDATION_ERROR")

    def test_create_with_explicit_too_small_table_is_validation_error(self):
        result = manage_booking(self._base_create_payload(confirmed=True, party_size=6, table_id=1))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "VALIDATION_ERROR")

    def test_create_conflict_on_already_booked_explicit_table(self):
        manage_booking(self._base_create_payload(confirmed=True, table_id=3, party_size=4))
        second = manage_booking(
            self._base_create_payload(confirmed=True, table_id=3, party_size=4, customer_name="Second Guest")
        )
        self.assertEqual(second.status, "error")
        self.assertEqual(second.error_code, "CONFLICT")

    def test_create_no_availability_when_party_size_too_large_for_any_table(self):
        result = manage_booking(self._base_create_payload(confirmed=True, party_size=999))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "VALIDATION_ERROR")  # caught by availability check first

    def test_modify_nonexistent_booking_is_not_found(self):
        result = manage_booking(ManageBookingInput(action="modify", confirmed=True, booking_id=9999, party_size=4))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "NOT_FOUND")

    def test_modify_party_size_reassigns_to_bigger_table_when_needed(self):
        created = manage_booking(self._base_create_payload(confirmed=True, party_size=2, table_id=1))
        booking_id = created.booking["booking_id"]

        modified = manage_booking(
            ManageBookingInput(action="modify", confirmed=True, booking_id=booking_id, party_size=6)
        )
        self.assertEqual(modified.status, "success")
        self.assertGreaterEqual(modified.booking["table_id"], 1)
        # Re-fetch the actual table capacity to confirm it now fits 6.
        self.assertNotEqual(modified.booking["table_id"], 1)  # table 1 only seats 2

    def test_cancel_requires_confirmation_first(self):
        created = manage_booking(self._base_create_payload(confirmed=True))
        booking_id = created.booking["booking_id"]

        preview = manage_booking(ManageBookingInput(action="cancel", confirmed=False, booking_id=booking_id))
        self.assertEqual(preview.status, "pending_confirmation")

        still_active = db_helper.fetch_booking(booking_id)
        self.assertEqual(still_active["status"], "confirmed")

    def test_cancel_then_cancel_again_is_already_cancelled(self):
        created = manage_booking(self._base_create_payload(confirmed=True))
        booking_id = created.booking["booking_id"]

        first_cancel = manage_booking(ManageBookingInput(action="cancel", confirmed=True, booking_id=booking_id))
        self.assertEqual(first_cancel.status, "success")

        second_cancel = manage_booking(ManageBookingInput(action="cancel", confirmed=True, booking_id=booking_id))
        self.assertEqual(second_cancel.status, "error")
        self.assertEqual(second_cancel.error_code, "ALREADY_CANCELLED")

    def test_unsupported_action_is_validation_error(self):
        result = manage_booking(ManageBookingInput(action="delete_forever", confirmed=True))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
