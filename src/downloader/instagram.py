import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yt_dlp

from src.config.settings import Settings
from src.downloader.models import DownloadResult, DownloadedFile, MediaKind

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """A user-facing download failure."""


class InstagramDownloader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.temp_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str) -> tuple[DownloadResult, Path]:
        job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=self.settings.temp_dir))
        try:
            result = await asyncio.wait_for(asyncio.to_thread(self._download_sync, url, job_dir), self.settings.download_timeout_seconds)
            return result, job_dir
        except asyncio.TimeoutError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise DownloadError("دانلود بیش از زمان مجاز طول کشید. لینک یا حجم محتوا را بررسی کنید.") from exc
        except DownloadError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        except Exception as exc:
            logger.exception("Unexpected downloader failure for %s", url)
            shutil.rmtree(job_dir, ignore_errors=True)
            raise DownloadError("دانلود انجام نشد؛ Instagram ممکن است محتوا را محدود کرده باشد.") from exc

    def _download_sync(self, url: str, job_dir: Path) -> DownloadResult:
        max_bytes = self.settings.max_file_size_mb * 1024 * 1024
        options: dict[str, Any] = {
            "outtmpl": str(job_dir / "%(title).80s-%(id)s.%(ext)s"),
            "format": "best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "noplaylist": False,
            "retries": self.settings.download_retries,
            "fragment_retries": self.settings.download_retries,
            "socket_timeout": 20,
            "max_filesize": max_bytes,
            "restrictfilenames": True,
            "quiet": True,
            "no_warnings": True,
            "cachedir": False,
        }
        try:
            with yt_dlp.YoutubeDL(options) as client:
                info = client.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            message = str(exc).lower()
            if "max-filesize" in message or "larger than max-filesize" in message:
                raise DownloadError(f"حجم فایل از سقف {self.settings.max_file_size_mb}MB تلگرام بیشتر است.") from exc
            raise DownloadError("لینک نامعتبر است یا محتوای آن عمومی و قابل‌دسترسی نیست.") from exc

        entries = info.get("entries") or [info]
        files: list[DownloadedFile] = []
        title = info.get("title") or "instagram-media"
        for entry in entries:
            if not entry:
                continue
            entry_title = entry.get("title") or title
            requested = entry.get("requested_downloads") or []
            candidates = [Path(item["filepath"]) for item in requested if item.get("filepath")]
            candidates.extend(Path(path) for path in job_dir.glob("*") if Path(path).is_file())
            for path in candidates:
                if not path.exists() or any(item.path == path for item in files):
                    continue
                if path.stat().st_size > max_bytes:
                    raise DownloadError(f"حجم فایل از سقف {self.settings.max_file_size_mb}MB تلگرام بیشتر است.")
                kind = MediaKind.VIDEO if path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"} else MediaKind.IMAGE
                files.append(DownloadedFile(path=path, kind=kind, title=entry_title))
        if not files:
            raise DownloadError("فایل قابل ارسال از این لینک پیدا نشد.")
        return DownloadResult(files=files, title=title, extractor=info.get("extractor_key", "instagram"))
