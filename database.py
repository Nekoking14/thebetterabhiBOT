"""Local SQLite lifecycle. Versioned, transactional migrations never discard data."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import PROJECT_DIR, load_environment

SCHEMA_VERSION = 1
APPLICATION_ID = 0x53445231  # SDR1: identifies this application's database.


def database_path():
    load_environment()
    path = Path(os.getenv("LOCAL_DB_PATH", "data/app.db")).expanduser()
    return path if path.is_absolute() else PROJECT_DIR / path


@contextmanager
def connection(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
    finally:
        db.close()


MIGRATION_1 = (
    """CREATE TABLE scrub_sessions (
        session_id INTEGER PRIMARY KEY, session_date TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE scrub_accounts (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES scrub_sessions(session_id),
        company_id TEXT, company_name TEXT NOT NULL, domain TEXT, name_key TEXT NOT NULL,
        country TEXT, city TEXT, industry TEXT, employee_count INTEGER,
        account_score INTEGER NOT NULL CHECK(account_score BETWEEN 0 AND 100),
        account_score_band TEXT NOT NULL,
        best_prospect_name TEXT, best_prospect_title TEXT, best_prospect_score INTEGER,
        source TEXT NOT NULL CHECK(source IN ('mock','leadiq')),
        status TEXT NOT NULL DEFAULT 'NEW' CHECK(status IN ('NEW','REVIEWED','REJECTED')),
        date_added TEXT NOT NULL, updated_at TEXT NOT NULL,
        matching_prospects INTEGER NOT NULL DEFAULT 0, prospects_json TEXT NOT NULL DEFAULT '[]',
        prospect_source TEXT NOT NULL CHECK(prospect_source IN ('mock','leadiq')),
        fallback_key TEXT,
        UNIQUE(session_id, fallback_key))""",
    """CREATE TABLE account_identities (
        session_id INTEGER NOT NULL REFERENCES scrub_sessions(session_id),
        kind TEXT NOT NULL CHECK(kind IN ('domain','provider')),
        value TEXT NOT NULL, account_id INTEGER NOT NULL REFERENCES scrub_accounts(id),
        PRIMARY KEY(session_id, kind, value))""",
    "CREATE INDEX accounts_rank ON scrub_accounts(session_id, account_score DESC, company_name, id)",
    "CREATE INDEX accounts_name ON scrub_accounts(session_id, name_key)",
)


def initialize_database(path=None):
    path = Path(path) if path is not None else database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as db:
        # Serialize first-run initialization and recheck the version after locking.
        with db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("Local database uses a newer schema. Upgrade the app; existing data was preserved.")
            if version and app_id != APPLICATION_ID:
                raise ValueError("This is not an Account Finder database. Choose another LOCAL_DB_PATH.")
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError("Unrecognized local database. Existing data was preserved; choose another LOCAL_DB_PATH.")
                for statement in MIGRATION_1:
                    db.execute(statement)
                db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                db.execute("PRAGMA user_version = 1")
    return path


def reset_local_database(path=None):
    """Called only after CLI confirmation. Refuse to delete unrelated files."""
    path = Path(path) if path is not None else database_path()
    if not path.exists():
        return False
    if path.is_symlink() or not path.is_file():
        raise ValueError("Refusing to reset a symlink or a non-file database path.")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("Refusing to delete a file that is not an Account Finder database.")
    path.unlink()
    for suffix in ("-wal", "-shm", "-journal"):
        companion = Path(str(path) + suffix)
        if companion.exists():
            companion.unlink()
    return True
