import logging
import shutil
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

from src.database.repository import DownloadRepository
from src.downloader.instagram import DownloadError, InstagramDownloader
from src.downloader.models import DownloadResult, MediaKind
from src.services.queue import DownloadJob

logger = logging.getLogger(__name__)


class MediaService:
    def __init__(self, bot: Bot, downloader: InstagramDownloader, repository: DownloadRepository) -> None:
        self.bot = bot
        self.downloader = downloader
        self.repository = repository

    async def process(self, job: DownloadJob, status_message) -> None:
        await status_message.edit_text("در حال بررسی و دانلود محتوا...\nلطفا چند لحظه صبر کن.")
        try:
            result, directory = await self.downloader.download(job.url)
            await self._send_result(job.user_id, result)
            self.repository.record(job.user_id, job.url, "success")
            await status_message.delete()
        except DownloadError as exc:
            self.repository.record(job.user_id, job.url, "failed")
            await status_message.edit_text(str(exc))
        except TelegramAPIError as exc:
            self.repository.record(job.user_id, job.url, "telegram_failed")
            logger.warning("Telegram rejected media for user %s: %s", job.user_id, exc)
            await status_message.edit_text(
                "فایل آماده شد، اما Telegram نتوانست آن را ارسال کند.\n"
                "احتمالا حجم یا نوع فایل با محدودیت Telegram سازگار نیست."
            )
        except Exception:
            self.repository.record(job.user_id, job.url, "error")
            logger.exception("Failed to process media job")
            await status_message.edit_text("یک خطای پیش‌بینی‌نشده رخ داد. لطفا کمی بعد دوباره تلاش کن.")
        finally:
            directory = locals().get("directory")
            if directory:
                shutil.rmtree(directory, ignore_errors=True)

    async def _send_result(self, user_id: int, result: DownloadResult) -> None:
        if len(result.files) == 1:
            item = result.files[0]
            caption = result.title[:900]
            if item.kind is MediaKind.VIDEO:
                await self.bot.send_video(user_id, FSInputFile(item.path), caption=caption)
            else:
                await self.bot.send_photo(user_id, FSInputFile(item.path), caption=caption)
            return
        # Telegram media groups are limited to 10 items; send larger carousels in chunks.
        for start in range(0, len(result.files), 10):
            group = result.files[start:start + 10]
            media = []
            for index, item in enumerate(group):
                caption = result.title[:900] if start == 0 and index == 0 else None
                if item.kind is MediaKind.VIDEO:
                    media.append(InputMediaVideo(media=FSInputFile(item.path), caption=caption))
                else:
                    media.append(InputMediaPhoto(media=FSInputFile(item.path), caption=caption))
            await self.bot.send_media_group(user_id, media=media)
