from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from src.config.settings import Settings
from src.database.repository import DownloadRepository
from src.downloader.instagram import InstagramDownloader
from src.handlers.media import register_media_handler, router
from src.services.media import MediaService
from src.services.queue import DownloadQueue


class Application:
    def __init__(self, settings: Settings) -> None:
        self.bot = Bot(settings.bot_token)
        self.dispatcher = Dispatcher()
        self.queue = DownloadQueue(settings.max_concurrent_downloads)
        repository = DownloadRepository(settings.database_path)
        downloader = InstagramDownloader(settings)
        service = MediaService(self.bot, downloader, repository)
        register_media_handler(self.queue, service)
        self.dispatcher.include_router(router)

    async def run(self) -> None:
        await self.queue.start()
        try:
            await self.bot.set_my_commands(
                [
                    BotCommand(command="start", description="شروع کار با ربات"),
                    BotCommand(command="creator", description="معرفی سازنده"),
                ]
            )
            await self.dispatcher.start_polling(self.bot)
        finally:
            await self.queue.stop()
            await self.bot.session.close()
