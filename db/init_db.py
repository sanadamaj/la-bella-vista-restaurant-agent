"""
init_db.py — Creates db/bookings.db from schema.sql and seeds a handful of
sample reservations so Phase 2's check_availability and manage_booking
tools have real data to query against during development.

Usage:
    python3 init_db.py            # create + seed (skips seeding if rows exist)
    python3 init_db.py --reset    # drop and recreate from scratch
"""

import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bookings.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

SAMPLE_BOOKINGS = [
    # customer_name, phone, party_size, booking_date, booking_time, table_id, status, special_requests
    ("Nadine Khalil",  "+961 3 123456", 2, "2026-06-20", "19:00", 1,  "confirmed", None),
    ("Marc Abou Jaoude", "+961 70 222333", 4, "2026-06-20", "20:00", 3,  "confirmed", "Birthday candle for dessert"),
    ("Sara Haddad",    "+961 76 555888", 6, "2026-06-21", "13:00", 10, "confirmed", None),
    ("Tony Fares",     "+961 3 999000",  2, "2026-06-19", "19:30", 9,  "cancelled", "Allergic to shellfish"),
    ("Lea Maalouf",    "+961 71 444777", 8, "2026-06-22", "20:30", 8,  "confirmed", "Private room requested"),
]

# A couple of returning customers with a visit history already on file, so
# the long-term memory feature (bonus) is demonstrable immediately rather
# than only after a fresh booking is made during testing.
SAMPLE_PREFERENCES = [
    # customer_name, phone, visit_count, last_party_size, last_location, last_booking_date
    ("Marc Abou Jaoude", "+961 70 222333", 3, 4, "indoor", "2026-05-10"),
    ("Sara Haddad", "+961 76 555888", 1, 6, "terrace", "2026-06-21"),
]


def create_schema(conn: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()


def seed_sample_data(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT COUNT(*) FROM bookings")
    existing_count = cur.fetchone()[0]
    if existing_count > 0:
        print(f"Skipping booking seed: {existing_count} booking(s) already present.")
    else:
        conn.executemany(
            """
            INSERT INTO bookings
                (customer_name, phone, party_size, booking_date, booking_time, table_id, status, special_requests)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            SAMPLE_BOOKINGS,
        )
        conn.commit()
        print(f"Seeded {len(SAMPLE_BOOKINGS)} sample booking(s).")

    cur = conn.execute("SELECT COUNT(*) FROM customer_preferences")
    existing_pref_count = cur.fetchone()[0]
    if existing_pref_count > 0:
        print(f"Skipping preferences seed: {existing_pref_count} customer record(s) already present.")
        return

    conn.executemany(
        """
        INSERT INTO customer_preferences
            (customer_name, phone, visit_count, last_party_size, last_location, last_booking_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        SAMPLE_PREFERENCES,
    )
    conn.commit()
    print(f"Seeded {len(SAMPLE_PREFERENCES)} sample customer preference record(s).")


def main() -> None:
    reset = "--reset" in sys.argv
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing database at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        create_schema(conn)
        seed_sample_data(conn)
        print(f"Database ready at {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
