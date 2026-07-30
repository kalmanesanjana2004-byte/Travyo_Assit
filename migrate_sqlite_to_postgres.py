"""
migrate_sqlite_to_postgres.py
─────────────────────────────
Safely migrates all data from the existing SQLite database (travyo.db)
into the PostgreSQL database specified by DATABASE_URL.

Usage:
    1. Make sure PostgreSQL is running and DATABASE_URL is set.
    2. Run:  python migrate_sqlite_to_postgres.py

The script will:
  • NOT delete or modify your SQLite file.
  • Create the PostgreSQL schema (safe — uses IF NOT EXISTS).
  • Insert every row from SQLite into PostgreSQL (skips duplicates by PK).
  • Reset PostgreSQL sequences so new INSERTs get correct IDs.
  • Print a verification table: SQLite count vs PostgreSQL count per table.
  • Roll back the whole PostgreSQL transaction on any error.
"""

import os
import sys
import sqlite3

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is not installed. Run:  pip install psycopg2-binary")
    sys.exit(1)

# ── Configuration ─────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH  = os.environ.get("SQLITE_PATH", os.path.join(BASE_DIR, "travyo.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ── Table definitions (PostgreSQL) ────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id         SERIAL PRIMARY KEY,
    username   TEXT    NOT NULL UNIQUE,
    name       TEXT    NOT NULL,
    email      TEXT    NOT NULL UNIQUE,
    password   TEXT    NOT NULL,
    role       TEXT    NOT NULL DEFAULT 'user',
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS properties (
    id          SERIAL PRIMARY KEY,
    name        TEXT    NOT NULL,
    location    TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'hotel',
    description TEXT,
    price       NUMERIC(10,2) NOT NULL DEFAULT 0,
    rating      NUMERIC(3,1)  NOT NULL DEFAULT 4.0,
    image_url   TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookings (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_id    INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    check_in       DATE,
    check_out      DATE,
    guests         INTEGER NOT NULL DEFAULT 1,
    rooms          INTEGER NOT NULL DEFAULT 1,
    total_price    NUMERIC(10,2) NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'pending_payment',
    payment_method TEXT,
    payment_id     TEXT,
    payment_status TEXT    NOT NULL DEFAULT 'pending',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    message    TEXT    NOT NULL,
    type       TEXT    DEFAULT 'general',
    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS requests (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    phone        TEXT,
    request_type TEXT DEFAULT 'property_request',
    subject      TEXT NOT NULL DEFAULT 'Property Request',
    message      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    admin_notes  TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activities (
    id      SERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    icon    TEXT DEFAULT 'fas fa-info-circle',
    time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Tables to migrate in dependency order ────────────────────────────────────
TABLES = ["users", "properties", "bookings", "notifications", "requests", "activities"]


def connect_sqlite():
    if not os.path.exists(SQLITE_PATH):
        print(f"  ⚠  SQLite database not found at: {SQLITE_PATH}")
        print("     (This is normal if the app was never started with SQLite.)")
        print("     Nothing to migrate — your PostgreSQL schema is already ready.")
        return None
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def connect_postgres():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def sqlite_count(sq_conn, table):
    try:
        row = sq_conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return row["c"] if row else 0
    except Exception:
        return 0


def pg_count(pg_cur, table):
    pg_cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
    row = pg_cur.fetchone()
    return row["c"] if row else 0


def get_sqlite_columns(sq_conn, table):
    rows = sq_conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def reset_sequence(pg_cur, table):
    """Reset the SERIAL sequence to max(id)+1 so future inserts don't collide."""
    pg_cur.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE(MAX(id), 0) + 1, false) FROM {table}"
    )


def migrate_table(sq_conn, pg_conn, table):
    """Copy all rows from SQLite table into PostgreSQL, skipping existing PKs."""
    cols = get_sqlite_columns(sq_conn, table)
    if not cols:
        print(f"  ⚠  Table '{table}' not found in SQLite — skipping.")
        return 0

    rows = sq_conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"  →  {table}: 0 rows (empty table).")
        return 0

    pg_cur  = pg_conn.cursor()
    col_str = ", ".join(cols)
    ph_str  = ", ".join(["%s"] * len(cols))

    inserted = 0
    skipped  = 0
    for row in rows:
        values = []
        for col in cols:
            val = row[col]
            # Convert SQLite integer booleans (0/1) to Python bool for BOOLEAN columns
            if col in ("is_active", "is_read") and isinstance(val, int):
                val = bool(val)
            values.append(val)

        try:
            pg_cur.execute(
                f"INSERT INTO {table} ({col_str}) VALUES ({ph_str})"
                f" ON CONFLICT (id) DO NOTHING",
                values,
            )
            if pg_cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            pg_conn.rollback()
            print(f"\n  ✗  Error inserting row into '{table}': {exc}")
            print(f"     Row data: {dict(zip(cols, values))}")
            raise

    reset_sequence(pg_cur, table)
    pg_cur.close()
    print(f"  ✓  {table}: {inserted} inserted, {skipped} skipped (already existed).")
    return inserted


def main():
    print("\n" + "="*60)
    print("  Travyo — SQLite → PostgreSQL Migration")
    print("="*60)

    print(f"\n[1] Connecting to SQLite:    {SQLITE_PATH}")
    sq_conn = connect_sqlite()

    print(f"[2] Connecting to PostgreSQL: {DATABASE_URL[:40]}...")
    pg_conn = connect_postgres()
    pg_conn.autocommit = False

    print("\n[3] Creating PostgreSQL schema (IF NOT EXISTS)...")
    try:
        pg_cur = pg_conn.cursor()
        for statement in SCHEMA_SQL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                pg_cur.execute(stmt)
        pg_conn.commit()
        pg_cur.close()
        print("    Schema ready.")
    except Exception as exc:
        pg_conn.rollback()
        print(f"    ✗ Schema creation failed: {exc}")
        sys.exit(1)

    if sq_conn is None:
        print("\n[4] No SQLite data to migrate. Done.\n")
        pg_conn.close()
        return

    print("\n[4] Migrating data...")
    try:
        for table in TABLES:
            migrate_table(sq_conn, pg_conn, table)
        pg_conn.commit()
        print("\n    All data committed successfully.")
    except Exception as exc:
        pg_conn.rollback()
        print(f"\n    ✗ Migration failed — rolled back. Error: {exc}")
        sq_conn.close()
        pg_conn.close()
        sys.exit(1)

    print("\n[5] Verification — record counts:")
    print(f"\n  {'Table':<20} {'SQLite':>10} {'PostgreSQL':>12} {'Status':>10}")
    print("  " + "-"*56)
    all_ok = True
    pg_cur = pg_conn.cursor()
    for table in TABLES:
        sq_c = sqlite_count(sq_conn, table)
        pg_c = pg_count(pg_cur, table)
        ok   = pg_c >= sq_c
        if not ok:
            all_ok = False
        status = "✓ OK" if ok else "✗ MISMATCH"
        print(f"  {table:<20} {sq_c:>10} {pg_c:>12} {status:>10}")
    pg_cur.close()

    sq_conn.close()
    pg_conn.close()

    print("\n" + "="*60)
    if all_ok:
        print("  ✅  Migration complete — all record counts match.")
    else:
        print("  ⚠   Migration finished with count mismatches.")
        print("      Review the table above and re-run if needed.")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
