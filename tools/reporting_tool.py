"""
reporting_tool.py - Reporting Tool (Tool 4 of 4).

Purpose
-------
Produces a customer-facing booking confirmation / visit summary for a
given booking_id, pulling the reservation from bookings.db and enriching
it with the matching table's details from tables.json.

Input schema (GenerateSummaryReportInput)
---------------------------------------------
booking_id   : int                                    (required)
report_format: "markdown" | "plain"   (default "markdown")

Output schema (GenerateSummaryReportOutput)
-----------------------------------------------
status       : "success" | "not_found"
report_text  : str | None
booking      : dict | None

Error behavior
---------------
A booking_id that doesn't exist in the database returns status="not_found"
with report_text=None rather than raising, so the workflow can route to a
"we couldn't find that booking" response instead of crashing.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from tools import data_loader, db_helper

VALID_FORMATS = {"markdown", "plain"}


@dataclass
class GenerateSummaryReportInput:
    booking_id: int
    report_format: str = "markdown"


@dataclass
class GenerateSummaryReportOutput:
    status: str  # "success" | "not_found"
    report_text: Optional[str] = None
    booking: Optional[Dict[str, Any]] = None


def _format_markdown(booking: Dict[str, Any], table: Optional[Dict[str, Any]]) -> str:
    table_desc = (
        f"Table {table['table_id']} ({table['location']}, seats {table['capacity']})"
        if table
        else f"Table {booking['table_id']}"
    )
    status_line = (
        "**CANCELLED**" if booking["status"] == "cancelled" else "**Confirmed**"
    )
    lines = [
        f"## La Bella Vista — Reservation #{booking['booking_id']}",
        "",
        f"Status: {status_line}",
        f"Guest: {booking['customer_name']}",
        f"Date: {booking['booking_date']}",
        f"Time: {booking['booking_time']}",
        f"Party size: {booking['party_size']}",
        f"Seating: {table_desc}",
    ]
    if booking.get("special_requests"):
        lines.append(f"Special requests: {booking['special_requests']}")
    lines.append("")
    lines.append(
        "Reservations can be changed or cancelled free of charge up to 2 hours "
        "before the booking time. We look forward to seeing you."
    )
    return "\n".join(lines)


def _format_plain(booking: Dict[str, Any], table: Optional[Dict[str, Any]]) -> str:
    table_desc = (
        f"Table {table['table_id']} ({table['location']}, seats {table['capacity']})"
        if table
        else f"Table {booking['table_id']}"
    )
    parts = [
        f"La Bella Vista Reservation #{booking['booking_id']}",
        f"Status: {booking['status']}",
        f"Guest: {booking['customer_name']}",
        f"Date/time: {booking['booking_date']} {booking['booking_time']}",
        f"Party size: {booking['party_size']}",
        f"Seating: {table_desc}",
    ]
    if booking.get("special_requests"):
        parts.append(f"Special requests: {booking['special_requests']}")
    return " | ".join(parts)


def generate_summary_report(payload: GenerateSummaryReportInput) -> GenerateSummaryReportOutput:
    booking = db_helper.fetch_booking(payload.booking_id)
    if booking is None:
        return GenerateSummaryReportOutput(status="not_found")

    table = data_loader.get_table_by_id(booking["table_id"])
    fmt = payload.report_format if payload.report_format in VALID_FORMATS else "markdown"
    report_text = _format_markdown(booking, table) if fmt == "markdown" else _format_plain(booking, table)

    return GenerateSummaryReportOutput(status="success", report_text=report_text, booking=booking)
