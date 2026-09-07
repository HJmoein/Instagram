import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo

from src.database.repository import DownloadRepository
from src.downloader.instagram import DownloadError, InstagramDownloader
from src.downloader.models import DownloadResult, MediaKind
from src.services.queue import DownloadJob
from src.services.captions import save_caption

logger = logging.getLogger(__name__)
MAX_VIDEO_CAPTION_LENGTH = 900
MAX_TELEGRAM_CAPTION_LENGTH = 1024


class MediaService:
    def __init__(self, bot: Bot, downloader: InstagramDownloader, repository: DownloadRepository) -> None:
        self.bot = bot
        self.downloader = downloader
        self.repository = repository

    async def process(self, job: DownloadJob, status_message) -> None:
        await status_message.edit_text(self._progress_text(0))
        loop = asyncio.get_running_loop()
        progress_futures = []
        last_percent = -2

        async def update_status(percent: int) -> None:
            try:
                await status_message.edit_text(self._progress_text(percent))
            except TelegramAPIError:
                logger.debug("Could not update download progress for user %s", job.user_id)

        def on_progress(progress: float) -> None:
            nonlocal last_percent
            percent = min(100, int(progress))
            if percent < 100 and percent - last_percent < 2:
                return
            last_percent = percent
            progress_futures.append(
                asyncio.run_coroutine_threadsafe(update_status(percent), loop)
            )

        try:
            result, directory = await self.downloader.download(job.url, progress_callback=on_progress)
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in progress_futures),
                return_exceptions=True,
            )
            await status_message.edit_text("✅ دانلود کامل شد\n\n📤 در حال ارسال فایل...")
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

    @staticmethod
    def _progress_text(percent: int) -> str:
        filled = percent // 5
        bar = "█" * filled + "░" * (20 - filled)
        return f"در حال دانلود...\n\n{bar} {percent}%\nلطفاً صبر کنید."

    async def _send_result(self, user_id: int, result: DownloadResult) -> None:
        if len(result.files) == 1:
            item = result.files[0]
            if item.kind is MediaKind.VIDEO:
                caption_key = save_caption(self._shorten_caption(result.caption or result.title))
                await self.bot.send_video(
                    user_id,
                    FSInputFile(item.path),
                    reply_markup=self._video_keyboard(caption_key),
                )
            else:
                await self.bot.send_photo(user_id, FSInputFile(item.path), caption=result.title[:900])
            return
        # Telegram media groups are limited to 10 items; send larger carousels in chunks.
        for start in range(0, len(result.files), 10):
            group = result.files[start:start + 10]
            media = []
            for index, item in enumerate(group):
                if item.kind is MediaKind.VIDEO:
                    media.append(InputMediaVideo(media=FSInputFile(item.path)))
                else:
                    media.append(InputMediaPhoto(media=FSInputFile(item.path), caption=result.title[:900] if start == 0 and index == 0 else None))
            await self.bot.send_media_group(user_id, media=media)
        if any(item.kind is MediaKind.VIDEO for item in result.files):
            await self._send_caption_button(user_id, result.caption or result.title)

    async def _send_caption_button(self, user_id: int, caption: str) -> None:
        if not caption.strip():
            return
        key = save_caption(self._shorten_caption(caption))
        await self.bot.send_message(
            user_id,
            "برای دیدن کپشن کلیپ روی دکمه زیر بزن:",
            reply_markup=self._caption_keyboard(key),
        )

    @staticmethod
    def _caption_keyboard(key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📝 نمایش کپشن", callback_data=f"caption:{key}")]]
        )

    @staticmethod
    def _video_keyboard(key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📝 نمایش کپشن", callback_data=f"caption:{key}"),
                    InlineKeyboardButton(text="🎙 تبدیل به ویس", callback_data="convert_to_voice"),
                ]
            ]
        )

    async def convert_video_to_voice(self, message) -> None:
        if not message.video:
            return

        with tempfile.TemporaryDirectory(prefix="instagram_voice_") as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "video.mp4"
            voice_path = temp_path / "voice.ogg"
            telegram_file = await self.bot.get_file(message.video.file_id)
            await self.bot.download_file(telegram_file.file_path, video_path)

            import imageio_ffmpeg

            await asyncio.to_thread(
                self._extract_audio,
                imageio_ffmpeg.get_ffmpeg_exe(),
                video_path,
                voice_path,
            )
            await self.bot.send_voice(message.chat.id, FSInputFile(voice_path))

    @staticmethod
    def _extract_audio(ffmpeg_path: str, video_path: Path, voice_path: Path) -> None:
        subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-c:a",
                "libopus",
                "-b:a",
                "128k",
                str(voice_path),
            ],
            check=True,
            capture_output=True,
        )

    @staticmethod
    def _shorten_caption(caption: str) -> str:
        caption = caption.strip()
        if len(caption) <= MAX_TELEGRAM_CAPTION_LENGTH:
            return caption
        return caption[:MAX_TELEGRAM_CAPTION_LENGTH - 3].rstrip() + "..."
