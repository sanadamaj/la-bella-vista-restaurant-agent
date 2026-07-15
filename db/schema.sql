-- =====================================================================
-- La Bella Vista — Bookings Database Schema
-- =====================================================================
-- Design rationale:
--   Static domain knowledge (menu, table inventory, FAQs) lives in the
--   JSON files under /data because it never changes during a session
--   and is read-only for the agent (Information Tool).
--
--   Reservation records are dynamic, must persist across restarts, and
--   are created/updated/cancelled by the agent at runtime (Action Tool).
--   That is exactly the case the project spec calls out for SQLite:
--   "a SQLite database when persistent records are required."
-- =====================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bookings (
    booking_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name      TEXT    NOT NULL,
    phone              TEXT,
    party_size         INTEGER NOT NULL CHECK (party_size > 0),
    booking_date       TEXT    NOT NULL,              -- ISO format: YYYY-MM-DD
    booking_time       TEXT    NOT NULL,               -- 24h format: HH:MM
    table_id           INTEGER NOT NULL,                -- references tables.json table_id
    status             TEXT    NOT NULL DEFAULT 'confirmed'
                         CHECK (status IN ('confirmed', 'cancelled', 'completed', 'no_show')),
    special_requests   TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Speeds up the availability-check query: "is this table free at this date/time?"
CREATE INDEX IF NOT EXISTS idx_bookings_date_table
    ON bookings (booking_date, table_id);

-- Speeds up lookups by customer name for short-term/long-term memory features.
CREATE INDEX IF NOT EXISTS idx_bookings_customer
    ON bookings (customer_name);

-- Keep updated_at accurate whenever a row is modified (e.g. cancellation).
CREATE TRIGGER IF NOT EXISTS trg_bookings_updated_at
AFTER UPDATE ON bookings
FOR EACH ROW
BEGIN
    UPDATE bookings SET updated_at = datetime('now') WHERE booking_id = OLD.booking_id;
END;

-- =====================================================================
-- Long-term memory (bonus feature) — returning-customer preferences,
-- persisted across separate sessions, not just within one conversation.
-- =====================================================================
-- Identity is matched on customer_name alone, which is a deliberate,
-- documented limitation for a course project (two different guests named
-- "Karim" would share one row) rather than a flaw to silently paper over.
-- A production system would key this on a phone number, login, or loyalty
-- ID instead. See PHASE4_NOTES.md.

CREATE TABLE IF NOT EXISTS customer_preferences (
    customer_name      TEXT PRIMARY KEY,
    phone              TEXT,
    visit_count        INTEGER NOT NULL DEFAULT 0,
    last_party_size    INTEGER,
    last_location      TEXT,             -- indoor | outdoor | terrace
    last_booking_date  TEXT,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS trg_preferences_updated_at
AFTER UPDATE ON customer_preferences
FOR EACH ROW
BEGIN
    UPDATE customer_preferences SET updated_at = datetime('now') WHERE customer_name = OLD.customer_name;
END;
