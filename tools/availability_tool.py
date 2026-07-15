"""
availability_tool.py - Analysis Tool (Tool 2 of 4).

Purpose
-------
Given a date, time, party size, and optional seating preferences, determine
which tables (if any) can seat the party without conflicting with an
existing confirmed reservation. Applies deterministic rules only: no model
call, no guessing - every decision can be traced back to tables.json,
service hours, and the bookings table.

This module also exposes `find_candidate_tables()`, which the booking tool
(Tool 3) reuses for auto-assigning a table on create and for re-validating
conflicts on modify, so the "is this table actually free" logic exists in
exactly one place.

Input schema (CheckAvailabilityInput)
--------------------------------------
booking_date        : str   "YYYY-MM-DD"            (required)
booking_time         : str   "HH:MM", 24h            (required)
party_size           : int   > 0                      (required)
location_preference  : str   "indoor"|"outdoor"|"terrace"|"any"  (default "any")
accessible_required  : bool                          (default False)

Output schema (CheckAvailabilityOutput)
-----------------------------------------
status               : "available" | "unavailable" | "invalid_request"
available_tables     : list[TableOption]
reason               : str | None      (set for "unavailable"/"invalid_request")
suggested_alternative_times : list[str]  (only populated for "unavailable")

Error behavior
---------------
Malformed dates/times, party sizes <= 0, or party sizes larger than the
biggest table in the restaurant never raise an exception — they are
reported back as status="invalid_request" with a human-readable reason, so
the calling agent can relay it to the user instead of crashing.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from tools import data_loader, db_helper

VALID_LOCATIONS = {"indoor", "outdoor", "terrace", "any"}


@dataclass
class CheckAvailabilityInput:
    booking_date: str
    booking_time: str
    party_size: int
    location_preference: str = "any"
    accessible_required: bool = False


@dataclass
class TableOption:
    table_id: int
    capacity: int
    location: str
    table_type: str
    accessible: bool


@dataclass
class CheckAvailabilityOutput:
    status: str  # "available" | "unavailable" | "invalid_request"
    available_tables: List[TableOption] = field(default_factory=list)
    reason: Optional[str] = None
    suggested_alternative_times: List[str] = field(default_factory=list)


def _parse_date(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _parse_time(time_str: str) -> Optional[int]:
    """Returns minutes-since-midnight, or None if not valid HH:MM."""
    try:
        t = datetime.strptime(time_str, "%H:%M")
        return t.hour * 60 + t.minute
    except (ValueError, TypeError):
        return None


def _minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _which_service_window(time_minutes: int) -> Optional[str]:
    """Returns 'lunch', 'dinner', or None if outside both windows, applying
    the 'last seating 30 minutes before close' rule from faqs.json."""
    hours = data_loader.get_service_hours()
    for window_name, window in hours.items():
        start = _parse_time(window["start"])
        end = _parse_time(window["end"])
        last_seating = end - 30
        if start <= time_minutes <= last_seating:
            return window_name
    return None


def _slot_overlaps(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def find_candidate_tables(
    booking_date: str,
    booking_time: str,
    party_size: int,
    location_preference: str = "any",
    accessible_required: bool = False,
    exclude_booking_id: Optional[int] = None,
) -> List[TableOption]:
    """Core deterministic rule: a table is a candidate if it's big enough,
    matches the optional location/accessibility filters, and has no
    confirmed booking whose slot overlaps the requested time.

    `exclude_booking_id` lets the booking tool re-check a booking being
    modified without it conflicting with itself.
    """
    slot_length = data_loader.get_slot_length_minutes()
    req_start = _parse_time(booking_time)
    req_end = req_start + slot_length

    occupied_table_ids = set()
    for existing in db_helper.fetch_bookings_for_date(booking_date):
        if exclude_booking_id is not None and existing["booking_id"] == exclude_booking_id:
            continue
        existing_start = _parse_time(existing["booking_time"])
        existing_end = existing_start + slot_length
        if _slot_overlaps(req_start, req_end, existing_start, existing_end):
            occupied_table_ids.add(existing["table_id"])

    candidates = []
    for table in data_loader.get_all_tables():
        if table["capacity"] < party_size:
            continue
        if location_preference != "any" and table["location"] != location_preference:
            continue
        if accessible_required and not table["accessible"]:
            continue
        if table["table_id"] in occupied_table_ids:
            continue
        candidates.append(
            TableOption(
                table_id=table["table_id"],
                capacity=table["capacity"],
                location=table["location"],
                table_type=table["table_type"],
                accessible=table["accessible"],
            )
        )

    # Best-fit first: smallest table that still satisfies the party size,
    # so a 2-top doesn't get burned on a party of 2 if a closer fit exists.
    candidates.sort(key=lambda t: t.capacity)
    return candidates


def _suggest_alternative_times(
    booking_date: str,
    party_size: int,
    location_preference: str,
    accessible_required: bool,
    original_time_minutes: int,
) -> List[str]:
    """Tries nearby 30-minute-stepped times within the same service window
    and returns up to 3 that actually have a free table."""
    window_name = _which_service_window(original_time_minutes)
    if window_name is None:
        return []
    hours = data_loader.get_service_hours()
    window = hours[window_name]
    window_start = _parse_time(window["start"])
    window_end = _parse_time(window["end"]) - 30  # last seating

    suggestions = []
    offsets = [30, -30, 60, -60, 90, -90]
    for offset in offsets:
        candidate_minutes = original_time_minutes + offset
        if candidate_minutes < window_start or candidate_minutes > window_end:
            continue
        candidate_time_str = _minutes_to_hhmm(candidate_minutes)
        candidates = find_candidate_tables(
            booking_date, candidate_time_str, party_size,
            location_preference, accessible_required,
        )
        if candidates:
            suggestions.append(candidate_time_str)
        if len(suggestions) >= 3:
            break
    return sorted(suggestions)


def check_availability(payload: CheckAvailabilityInput) -> CheckAvailabilityOutput:
    # --- Validation ---
    parsed_date = _parse_date(payload.booking_date)
    if parsed_date is None:
        return CheckAvailabilityOutput(
            status="invalid_request",
            reason=f"'{payload.booking_date}' is not a valid date in YYYY-MM-DD format.",
        )
    if parsed_date.date() < datetime.now().date():
        return CheckAvailabilityOutput(
            status="invalid_request",
            reason=f"'{payload.booking_date}' is in the past. Please choose a future date.",
        )

    time_minutes = _parse_time(payload.booking_time)
    if time_minutes is None:
        return CheckAvailabilityOutput(
            status="invalid_request",
            reason=f"'{payload.booking_time}' is not a valid 24-hour HH:MM time.",
        )

    if payload.party_size <= 0:
        return CheckAvailabilityOutput(
            status="invalid_request",
            reason="Party size must be at least 1.",
        )

    max_capacity = data_loader.get_max_table_capacity()
    if payload.party_size > max_capacity:
        return CheckAvailabilityOutput(
            status="invalid_request",
            reason=(
                f"We don't have a table for {payload.party_size} guests; "
                f"our largest table seats {max_capacity}. For larger groups, "
                "please contact us directly about a private event."
            ),
        )

    if payload.location_preference not in VALID_LOCATIONS:
        return CheckAvailabilityOutput(
            status="invalid_request",
            reason=(
                f"'{payload.location_preference}' is not a recognized seating "
                f"area. Choose from: {', '.join(sorted(VALID_LOCATIONS))}."
            ),
        )

    window_name = _which_service_window(time_minutes)
    if window_name is None:
        hours = data_loader.get_service_hours()
        windows_desc = "; ".join(
            f"{name} {w['start']}-{w['end']}" for name, w in hours.items()
        )
        return CheckAvailabilityOutput(
            status="invalid_request",
            reason=(
                f"'{payload.booking_time}' falls outside our service hours "
                f"({windows_desc}), or within 30 minutes of closing."
            ),
        )

    # --- Core deterministic rule ---
    candidates = find_candidate_tables(
        payload.booking_date,
        payload.booking_time,
        payload.party_size,
        payload.location_preference,
        payload.accessible_required,
    )

    if candidates:
        return CheckAvailabilityOutput(status="available", available_tables=candidates[:5])

    alternatives = _suggest_alternative_times(
        payload.booking_date,
        payload.party_size,
        payload.location_preference,
        payload.accessible_required,
        time_minutes,
    )
    return CheckAvailabilityOutput(
        status="unavailable",
        reason="No tables matching your criteria are free at that date and time.",
        suggested_alternative_times=alternatives,
    )
