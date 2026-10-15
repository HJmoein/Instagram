import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

from src.downloader.models import CachedDownload, CachedMedia, MediaKind

logger = logging.getLogger(__name__)


class DownloadRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS downloads "
                "(id INTEGER PRIMARY KEY, user_id INTEGER, url TEXT, status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS media_cache "
                "(url_key TEXT PRIMARY KEY, files_json TEXT NOT NULL, title TEXT NOT NULL, "
                "caption TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record(self, user_id: int, url: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO downloads (user_id, url, status) VALUES (?, ?, ?)",
                (user_id, url, status),
            )

    @staticmethod
    def cache_key(url: str) -> str:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))

    def get_cached(self, url: str, ttl_days: int) -> CachedDownload | None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM media_cache WHERE created_at < datetime('now', ?)",
                (f"-{ttl_days} days",),
            )
            row = connection.execute(
                "SELECT files_json, title, caption FROM media_cache "
                "WHERE url_key = ? AND created_at >= datetime('now', ?)",
                (self.cache_key(url), f"-{ttl_days} days"),
            ).fetchone()
        if row is None:
            return None
        try:
            files = [
                CachedMedia(file_id=item["file_id"], kind=MediaKind(item["kind"]))
                for item in json.loads(row[0])
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Discarding malformed cached media entry for %s", self.cache_key(url))
            with self._connect() as connection:
                connection.execute("DELETE FROM media_cache WHERE url_key = ?", (self.cache_key(url),))
            return None
        if not files or any(not item.file_id for item in files):
            logger.warning("Discarding empty cached media entry for %s", self.cache_key(url))
            with self._connect() as connection:
                connection.execute("DELETE FROM media_cache WHERE url_key = ?", (self.cache_key(url),))
            return None
        return CachedDownload(files=files, title=row[1], caption=row[2])

    def save_cached(self, url: str, cached: CachedDownload) -> None:
        files_json = json.dumps(
            [{"file_id": item.file_id, "kind": item.kind.value} for item in cached.files],
            ensure_ascii=True,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO media_cache (url_key, files_json, title, caption) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(url_key) DO UPDATE SET files_json = excluded.files_json, "
                "title = excluded.title, caption = excluded.caption, created_at = CURRENT_TIMESTAMP",
                (self.cache_key(url), files_json, cached.title, cached.caption),
            )

    def delete_cached(self, url: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM media_cache WHERE url_key = ?", (self.cache_key(url),))
