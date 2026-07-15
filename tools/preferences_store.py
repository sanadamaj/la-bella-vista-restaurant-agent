"""
preferences_store.py — Long-term memory (bonus feature).

Persists a lightweight visit history per customer_name across SEPARATE
sessions — this is the genuine "long-term, cross-session" memory the
proposal distinguishes from the mandatory short-term/working memory,
which only needs to last for one active conversation.

Known, documented limitation: identity is matched on customer_name alone,
since this project has no login/auth system. Two different guests who
happen to share a name would be treated as the same returning customer.
That's an acceptable simplification for a course project bonus feature,
not something a real production system should do — see PHASE4_NOTES.md.

Like db_helper.py, this reuses the same bookings.db file (just a second
table in it) rather than introducing a separate database, since the
proposal explicitly allows long-term memory to live in "a file or
database" without requiring a dedicated one.
"""

from typing import Any, Dict, Optional

from tools.db_helper import get_connection


def get_preferences(customer_name: str) -> Optional[Dict[str, Any]]:
    """Returns the stored preference record for this customer name, or
    None if they've never been seen before (a brand new customer, not an
    error)."""
    if not customer_name:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customer_preferences WHERE customer_name = ?", (customer_name,)
        ).fetchone()
        return dict(row) if row else None


def upsert_after_booking(
    customer_name: str,
    phone: Optional[str],
    party_size: int,
    location: Optional[str],
    booking_date: str,
) -> None:
    """Called after a successful booking create. Increments visit_count if
    the customer already exists, or inserts a fresh row with visit_count=1
    if this is their first ever recorded visit."""
    if not customer_name:
        return
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT visit_count FROM customer_preferences WHERE customer_name = ?", (customer_name,)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE customer_preferences
                SET phone = COALESCE(?, phone),
                    visit_count = visit_count + 1,
                    last_party_size = ?,
                    last_location = COALESCE(?, last_location),
                    last_booking_date = ?
                WHERE customer_name = ?
                """,
                (phone, party_size, location, booking_date, customer_name),
            )
        else:
            conn.execute(
                """
                INSERT INTO customer_preferences
                    (customer_name, phone, visit_count, last_party_size, last_location, last_booking_date)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (customer_name, phone, party_size, location, booking_date),
            )
        conn.commit()
