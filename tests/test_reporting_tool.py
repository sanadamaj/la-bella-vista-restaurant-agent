import unittest
from pathlib import Path

from tools import db_helper
from tools.booking_tool import ManageBookingInput, manage_booking
from tools.reporting_tool import GenerateSummaryReportInput, generate_summary_report
from tests.test_helpers import make_temp_db, future_date


class TestReportingTool(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db_helper.DB_PATH
        db_helper.DB_PATH = Path(make_temp_db())
        self.date = future_date(5)

    def tearDown(self):
        Path(db_helper.DB_PATH).unlink(missing_ok=True)
        db_helper.DB_PATH = self._original_db_path

    def _create_booking(self):
        result = manage_booking(
            ManageBookingInput(
                action="create", confirmed=True, customer_name="Report Guest",
                party_size=2, booking_date=self.date, booking_time="19:00",
            )
        )
        return result.booking["booking_id"]

    def test_report_for_existing_booking_includes_key_fields(self):
        booking_id = self._create_booking()
        result = generate_summary_report(GenerateSummaryReportInput(booking_id=booking_id))
        self.assertEqual(result.status, "success")
        self.assertIn("Report Guest", result.report_text)
        self.assertIn(self.date, result.report_text)
        self.assertIn("19:00", result.report_text)

    def test_report_for_missing_booking_is_not_found(self):
        result = generate_summary_report(GenerateSummaryReportInput(booking_id=99999))
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.report_text)

    def test_plain_format_differs_from_markdown(self):
        booking_id = self._create_booking()
        md = generate_summary_report(GenerateSummaryReportInput(booking_id=booking_id, report_format="markdown"))
        plain = generate_summary_report(GenerateSummaryReportInput(booking_id=booking_id, report_format="plain"))
        self.assertNotEqual(md.report_text, plain.report_text)

    def test_cancelled_booking_report_shows_cancelled_status(self):
        booking_id = self._create_booking()
        manage_booking(ManageBookingInput(action="cancel", confirmed=True, booking_id=booking_id))
        result = generate_summary_report(GenerateSummaryReportInput(booking_id=booking_id))
        self.assertIn("CANCELLED", result.report_text)


if __name__ == "__main__":
    unittest.main()
