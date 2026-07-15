"""
booking_tool.py - Action Tool (Tool 3 of 4).

Purpose
-------
Creates, modifies, or cancels a reservation in bookings.db. This is the
state-changing tool, so per the project spec it must require explicit
user confirmation before committing anything.

Input schema (ManageBookingInput)
------------------------------------
action               : "create" | "modify" | "cancel"          (required)
confirmed            : bool, default False                      (required gate)
booking_id           : int | None   (required for modify/cancel)
customer_name        : str | None   (required for create)
phone                : str | None
party_size           : int | None   (required for create)
booking_date         : str | None   (required for create)
booking_time         : str | None   (required for create)
table_id             : int | None   (auto-assigned on create if omitted)
special_requests     : str | None

Output schema (ManageBookingOutput)
---------------------------------------
status        : "pending_confirmation" | "success" | "error"
booking       : dict | None     (final or proposed record)
message       : str
error_code    : str | None   one of VALIDATION_ERROR, NOT_FOUND,
                              ALREADY_CANCELLED, NO_AVAILABILITY, CONFLICT

Error behavior
---------------
Every failure path returns status="error" with an error_code rather than
raising - the workflow layer is expected to branch on these codes rather
than catch exceptions. The one exception is genuinely unexpected DB errors,
which are allowed to propagate so they surface in logs.

Confirmation gate
------------------
Calling this tool with confirmed=False (the default) NEVER writes to the
database. It always returns status="pending_confirmation" with a preview
of what *would* happen, so the orchestration layer can show that preview
to the user and only call again with confirmed=True after they agree.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from tools import data_loader, db_helper
from tools.availability_tool import (
    CheckAvailabilityInput,
    check_availability,
    find_candidate_tables,
)

VALID_ACTIONS = {"create", "modify", "cancel"}


@dataclass
class ManageBookingInput:
    action: str
    confirmed: bool = False
    booking_id: Optional[int] = None
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    party_size: Optional[int] = None
    booking_date: Optional[str] = None
    booking_time: Optional[str] = None
    table_id: Optional[int] = None
    special_requests: Optional[str] = None


@dataclass
class ManageBookingOutput:
    status: str  # "pending_confirmation" | "success" | "error"
    booking: Optional[Dict[str, Any]] = None
    message: str = ""
    error_code: Optional[str] = None


def _error(code: str, message: str) -> ManageBookingOutput:
    return ManageBookingOutput(status="error", error_code=code, message=message)


def _resolve_table_for_create(payload: ManageBookingInput) -> "tuple[Optional[int], Optional[ManageBookingOutput]]":
    """Returns (table_id, None) on success or (None, error_output) on failure."""
    if payload.table_id is not None:
        table = data_loader.get_table_by_id(payload.table_id)
        if table is None:
            return None, _error("VALIDATION_ERROR", f"Table {payload.table_id} does not exist.")
        if table["capacity"] < payload.party_size:
            return None, _error(
                "VALIDATION_ERROR",
                f"Table {payload.table_id} seats {table['capacity']}, "
                f"too small for a party of {payload.party_size}.",
            )
        candidates = find_candidate_tables(
            payload.booking_date, payload.booking_time, payload.party_size,
        )
        if not any(c.table_id == payload.table_id for c in candidates):
            return None, _error(
                "CONFLICT",
                f"Table {payload.table_id} is already booked at that date and time.",
            )
        return payload.table_id, None

    # Auto-assign the best-fit free table.
    candidates = find_candidate_tables(
        payload.booking_date, payload.booking_time, payload.party_size,
    )
    if not candidates:
        return None, _error(
            "NO_AVAILABILITY",
            "No table is free for that party size at that date and time.",
        )
    return candidates[0].table_id, None


def _handle_create(payload: ManageBookingInput) -> ManageBookingOutput:
    missing = [
        field
        for field, value in (
            ("customer_name", payload.customer_name),
            ("party_size", payload.party_size),
            ("booking_date", payload.booking_date),
            ("booking_time", payload.booking_time),
        )
        if value in (None, "")
    ]
    if missing:
        return _error(
            "VALIDATION_ERROR",
            f"Missing required field(s) for a new booking: {', '.join(missing)}.",
        )

    # Reuse the availability tool's validation (date/time format, service
    # hours, party size vs. max capacity) instead of duplicating it.
    availability = check_availability(
        CheckAvailabilityInput(
            booking_date=payload.booking_date,
            booking_time=payload.booking_time,
            party_size=payload.party_size,
        )
    )
    if availability.status == "invalid_request":
        return _error("VALIDATION_ERROR", availability.reason)

    table_id, error = _resolve_table_for_create(payload)
    if error:
        return error

    preview = {
        "customer_name": payload.customer_name,
        "phone": payload.phone,
        "party_size": payload.party_size,
        "booking_date": payload.booking_date,
        "booking_time": payload.booking_time,
        "table_id": table_id,
        "special_requests": payload.special_requests,
        "status": "confirmed",
    }

    if not payload.confirmed:
        return ManageBookingOutput(
            status="pending_confirmation",
            booking=preview,
            message=(
                f"Please confirm: table for {payload.party_size} on "
                f"{payload.booking_date} at {payload.booking_time} "
                f"(table {table_id}). Reply to confirm or cancel."
            ),
        )

    new_id = db_helper.insert_booking(
        customer_name=payload.customer_name,
        phone=payload.phone,
        party_size=payload.party_size,
        booking_date=payload.booking_date,
        booking_time=payload.booking_time,
        table_id=table_id,
        special_requests=payload.special_requests,
    )
    created = db_helper.fetch_booking(new_id)
    return ManageBookingOutput(
        status="success",
        booking=created,
        message=f"Booking #{new_id} confirmed for {payload.booking_date} at {payload.booking_time}.",
    )


def _handle_modify(payload: ManageBookingInput) -> ManageBookingOutput:
    if payload.booking_id is None:
        return _error("VALIDATION_ERROR", "booking_id is required to modify a booking.")

    existing = db_helper.fetch_booking(payload.booking_id)
    if existing is None:
        return _error("NOT_FOUND", f"No booking found with id {payload.booking_id}.")
    if existing["status"] == "cancelled":
        return _error(
            "ALREADY_CANCELLED",
            f"Booking #{payload.booking_id} was already cancelled and can't be modified.",
        )

    # Build the proposed new state, falling back to existing values for
    # any field the caller didn't supply.
    new_date = payload.booking_date or existing["booking_date"]
    new_time = payload.booking_time or existing["booking_time"]
    new_party = payload.party_size or existing["party_size"]
    new_table = payload.table_id  # may be None -> re-check current table

    availability = check_availability(
        CheckAvailabilityInput(booking_date=new_date, booking_time=new_time, party_size=new_party)
    )
    if availability.status == "invalid_request":
        return _error("VALIDATION_ERROR", availability.reason)

    if new_table is None:
        new_table = existing["table_id"]
        table = data_loader.get_table_by_id(new_table)
        if table is None or table["capacity"] < new_party:
            # Existing table no longer fits a bigger party -> auto-reassign.
            candidates = find_candidate_tables(
                new_date, new_time, new_party, exclude_booking_id=payload.booking_id
            )
            if not candidates:
                return _error("NO_AVAILABILITY", "No table is free for the requested change.")
            new_table = candidates[0].table_id
    else:
        table = data_loader.get_table_by_id(new_table)
        if table is None:
            return _error("VALIDATION_ERROR", f"Table {new_table} does not exist.")
        if table["capacity"] < new_party:
            return _error(
                "VALIDATION_ERROR",
                f"Table {new_table} seats {table['capacity']}, too small for {new_party} guests.",
            )
        candidates = find_candidate_tables(
            new_date, new_time, new_party, exclude_booking_id=payload.booking_id
        )
        if not any(c.table_id == new_table for c in candidates):
            return _error("CONFLICT", f"Table {new_table} is already booked at that time.")

    preview = {
        "booking_id": payload.booking_id,
        "customer_name": payload.customer_name or existing["customer_name"],
        "phone": payload.phone or existing["phone"],
        "party_size": new_party,
        "booking_date": new_date,
        "booking_time": new_time,
        "table_id": new_table,
        "special_requests": (
            payload.special_requests if payload.special_requests is not None else existing["special_requests"]
        ),
        "status": "confirmed",
    }

    if not payload.confirmed:
        return ManageBookingOutput(
            status="pending_confirmation",
            booking=preview,
            message=(
                f"Please confirm changes to booking #{payload.booking_id}: "
                f"{new_party} guests on {new_date} at {new_time} (table {new_table})."
            ),
        )

    update_fields = {k: v for k, v in preview.items() if k != "booking_id"}
    db_helper.update_booking(payload.booking_id, update_fields)
    updated = db_helper.fetch_booking(payload.booking_id)
    return ManageBookingOutput(
        status="success",
        booking=updated,
        message=f"Booking #{payload.booking_id} updated.",
    )


def _handle_cancel(payload: ManageBookingInput) -> ManageBookingOutput:
    if payload.booking_id is None:
        return _error("VALIDATION_ERROR", "booking_id is required to cancel a booking.")

    existing = db_helper.fetch_booking(payload.booking_id)
    if existing is None:
        return _error("NOT_FOUND", f"No booking found with id {payload.booking_id}.")
    if existing["status"] == "cancelled":
        return _error(
            "ALREADY_CANCELLED",
            f"Booking #{payload.booking_id} is already cancelled.",
        )

    if not payload.confirmed:
        return ManageBookingOutput(
            status="pending_confirmation",
            booking=existing,
            message=(
                f"Please confirm you want to cancel booking #{payload.booking_id} "
                f"({existing['booking_date']} at {existing['booking_time']})."
            ),
        )

    db_helper.cancel_booking_row(payload.booking_id)
    updated = db_helper.fetch_booking(payload.booking_id)
    return ManageBookingOutput(
        status="success",
        booking=updated,
        message=f"Booking #{payload.booking_id} has been cancelled.",
    )


def manage_booking(payload: ManageBookingInput) -> ManageBookingOutput:
    if payload.action not in VALID_ACTIONS:
        return _error(
            "VALIDATION_ERROR",
            f"'{payload.action}' is not a supported action. Use one of: {', '.join(sorted(VALID_ACTIONS))}.",
        )

    if payload.action == "create":
        return _handle_create(payload)
    if payload.action == "modify":
        return _handle_modify(payload)
    return _handle_cancel(payload)
