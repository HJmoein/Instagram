import sqlite3
from pathlib import Path


class DownloadRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS downloads "
                "(id INTEGER PRIMARY KEY, user_id INTEGER, url TEXT, status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )

    def record(self, user_id: int, url: str, status: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO downloads (user_id, url, status) VALUES (?, ?, ?)",
                (user_id, url, status),
            )
