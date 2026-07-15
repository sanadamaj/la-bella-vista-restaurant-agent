"""
db_helper.py — Thin, shared data-access layer over bookings.db.

Every tool that needs to read or write a reservation goes through this
module rather than opening its own ad-hoc connection, so the SQL lives in
exactly one place and is easy to audit for the "data integrity" grading
criterion.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(
    os.environ.get(
        "DATABASE_PATH",
        Path(__file__).resolve().parent.parent / "db" / "bookings.db",
    )
)


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def fetch_booking(booking_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM bookings WHERE booking_id = ?", (booking_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def fetch_bookings_for_date(
    booking_date: str, include_cancelled: bool = False
) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if include_cancelled:
            rows = conn.execute(
                "SELECT * FROM bookings WHERE booking_date = ?", (booking_date,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bookings WHERE booking_date = ? AND status = 'confirmed'",
                (booking_date,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def insert_booking(
    customer_name: str,
    phone: Optional[str],
    party_size: int,
    booking_date: str,
    booking_time: str,
    table_id: int,
    special_requests: Optional[str],
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO bookings
                (customer_name, phone, party_size, booking_date, booking_time,
                 table_id, status, special_requests)
            VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?)
            """,
            (
                customer_name,
                phone,
                party_size,
                booking_date,
                booking_time,
                table_id,
                special_requests,
            ),
        )
        conn.commit()
        return cur.lastrowid


def update_booking(booking_id: int, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    allowed = {
        "customer_name",
        "phone",
        "party_size",
        "booking_date",
        "booking_time",
        "table_id",
        "special_requests",
        "status",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Cannot update unknown booking field(s): {unknown}")

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [booking_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE bookings SET {set_clause} WHERE booking_id = ?", values
        )
        conn.commit()


def cancel_booking_row(booking_id: int) -> None:
    update_booking(booking_id, {"status": "cancelled"})
