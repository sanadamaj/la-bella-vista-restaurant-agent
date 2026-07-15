"""
test_helpers.py — Sets up an isolated, temporary SQLite database for each
test run so the test suite never reads or writes the real bookings.db used
during manual/dev testing.
"""

import os
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"


def make_temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    return path


def future_date(days_ahead: int = 3) -> str:
    """Returns an ISO date string guaranteed to be in the future relative
    to whenever the test suite actually runs, instead of a hardcoded date
    that would eventually become 'the past' and break the suite."""
    return (date.today() + timedelta(days=days_ahead)).isoformat()
