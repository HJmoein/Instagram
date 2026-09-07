import asyncio
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bot.factory import Application
from src.config.settings import Settings
from src.utils.logging import configure_logging


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    logging.getLogger(__name__).info("Starting Instagram downloader bot")
    asyncio.run(Application(settings).run())


if __name__ == "__main__":
    main()
