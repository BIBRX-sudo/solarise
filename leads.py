import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB_NAME = "solarise_leads.db"
DB_NAME = os.getenv("LEADS_DB_PATH", DEFAULT_DB_NAME)


def get_db_path() -> str:
    """Returns the active database path.
    Prioritizes runtime overrides (e.g. leads.DB_NAME modified by test fixtures),
    then the LEADS_DB_PATH environment variable, and defaults to solarise_leads.db.
    """
    if DB_NAME and DB_NAME != DEFAULT_DB_NAME and DB_NAME != os.getenv("LEADS_DB_PATH"):
        return DB_NAME
    env_path = os.getenv("LEADS_DB_PATH")
    if env_path and env_path.strip():
        return env_path.strip()
    return DB_NAME or DEFAULT_DB_NAME


def initialize_database():
    target_db = get_db_path()
    db_dir = os.path.dirname(target_db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with sqlite3.connect(target_db) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                property_type TEXT NOT NULL,
                location TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'New',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add status to an existing database without deleting leads.
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(leads)"
            ).fetchall()
        }

        if "status" not in columns:
            connection.execute("""
                ALTER TABLE leads
                ADD COLUMN status TEXT NOT NULL DEFAULT 'New'
            """)


def save_lead(name: str, property_type: str, location: str):
    initialize_database()

    with sqlite3.connect(get_db_path()) as connection:
        cursor = connection.execute("""
            INSERT INTO leads (name, property_type, location)
            VALUES (?, ?, ?)
        """, (name, property_type, location))
        return cursor.lastrowid


def get_leads():
    initialize_database()

    with sqlite3.connect(get_db_path()) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM leads ORDER BY id DESC"
        ).fetchall()

        return [dict(row) for row in rows]


def update_lead_status(lead_id: int, status: str):
    allowed_statuses = {"New", "Contacted", "Converted"}
    clean_status = status.strip().title() if isinstance(status, str) else status

    if clean_status not in allowed_statuses:
        raise ValueError(
            "Status must be New, Contacted, or Converted"
        )

    initialize_database()

    with sqlite3.connect(get_db_path()) as connection:
        cursor = connection.execute(
            "UPDATE leads SET status = ? WHERE id = ?",
            (clean_status, lead_id),
        )

        return cursor.rowcount > 0
