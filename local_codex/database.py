"""SQLite schema baseline and recoverable upgrades for local state."""

from pathlib import Path
import sqlite3


SCHEMA_VERSION = 1


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path, timeout=10, check_same_thread=False)
    try:
        version = database.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Database was created by a newer LocalCodex version")
        if version < SCHEMA_VERSION:
            tables = database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                backup_path = path.with_name(path.name + f".schema-{version}.bak")
                if backup_path.exists():
                    raise RuntimeError(
                        f"Schema backup already exists; inspect it before retrying: {backup_path}"
                    )
                backup = sqlite3.connect(backup_path)
                try:
                    database.backup(backup)
                finally:
                    backup.close()
            # Version 1 adopts the existing tables unchanged. Future migrations
            # must update this marker only after their transaction succeeds.
            database.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        database.execute("PRAGMA journal_mode=WAL")
        return database
    except BaseException:
        database.close()
        raise
