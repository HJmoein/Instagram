from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    log_level: str
    max_concurrent_downloads: int
    download_timeout_seconds: int
    download_retries: int
    max_file_size_mb: int
    temp_dir: Path
    database_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("BOT_TOKEN is required in .env")
        temp_dir = Path(os.getenv("TEMP_DIR", "./tmp")).resolve()
        database_path = Path(os.getenv("DATABASE_PATH", "./data/bot.sqlite3")).resolve()
        return cls(
            bot_token=token,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            max_concurrent_downloads=max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))),
            download_timeout_seconds=max(10, int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "120"))),
            download_retries=max(0, int(os.getenv("DOWNLOAD_RETRIES", "2"))),
            max_file_size_mb=max(1, int(os.getenv("MAX_FILE_SIZE_MB", "49"))),
            temp_dir=temp_dir,
            database_path=database_path,
        )
