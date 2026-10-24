import asyncio
import logging
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from src.database.repository import DownloadRepository
from src.downloader.instagram import DownloadError, InstagramDownloader
from src.downloader.models import CachedDownload, CachedMedia, DownloadResult, MediaKind
from src.services.captions import save_caption
from src.services.queue import DownloadJob

logger = logging.getLogger(__name__)
MAX_VIDEO_CAPTION_LENGTH = 900
MAX_TELEGRAM_CAPTION_LENGTH = 1024
BOT_USERNAME = "@InstasDL_bot"


class MediaService:
    def __init__(self, bot: Bot, downloader: InstagramDownloader, repository: DownloadRepository) -> None:
        self.bot = bot
        self.downloader = downloader
        self.repository = repository
        self._url_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def _url_lock(self, key: str) -> AsyncIterator[None]:
        lock, users = self._url_locks.get(key, (asyncio.Lock(), 0))
        self._url_locks[key] = (lock, users + 1)
        acquired = False
        try:
            await lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            remaining = self._url_locks[key][1] - 1
            if remaining:
                self._url_locks[key] = (lock, remaining)
            else:
                del self._url_locks[key]

    async def process(self, job: DownloadJob, status_message: Message) -> None:
        cache_key = self.repository.cache_key(job.url)
        directory: Path | None = None
        async with self._url_lock(cache_key):
            try:
                cached = await asyncio.to_thread(
                    self.repository.get_cached,
                    job.url,
                    self.downloader.settings.cache_ttl_days,
                )
                if cached:
                    try:
                        await self._send_cached(job.user_id, cached)
                    except TelegramBadRequest as exc:
                        logger.warning("Cached Telegram media became unavailable: %s", exc)
                        await asyncio.to_thread(self.repository.delete_cached, job.url)
                        await status_message.edit_text("کش قبلی در دسترس نبود؛ در حال دریافت دوباره...")
                    else:
                        await self._record(job, "cache_hit")
                        await status_message.delete()
                        return

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
                    progress_futures.append(asyncio.run_coroutine_threadsafe(update_status(percent), loop))

                result, directory = await self.downloader.download(job.url, progress_callback=on_progress)
                await asyncio.gather(
                    *(asyncio.wrap_future(future) for future in progress_futures),
                    return_exceptions=True,
                )
                await status_message.edit_text("✅ دانلود کامل شد\n\n📤 در حال ارسال فایل...")
                files = await self._send_result(job.user_id, result)
                await self._save_cache(job.url, CachedDownload(files, result.title, result.caption))
                await self._record(job, "success")
                await status_message.delete()
            except DownloadError as exc:
                await self._record(job, "failed")
                await status_message.edit_text(str(exc))
            except TelegramAPIError as exc:
                await self._record(job, "telegram_failed")
                logger.warning("Telegram rejected media for user %s: %s", job.user_id, exc)
                await status_message.edit_text(
                    "فایل آماده شد، اما Telegram نتوانست آن را ارسال کند.\n"
                    "احتمالا حجم یا نوع فایل با محدودیت Telegram سازگار نیست."
                )
            except Exception:
                await self._record(job, "error")
                logger.exception("Failed to process media job")
                await status_message.edit_text("یک خطای پیش‌بینی‌نشده رخ داد. لطفا کمی بعد دوباره تلاش کن.")
            finally:
                if directory:
                    shutil.rmtree(directory, ignore_errors=True)

    async def _record(self, job: DownloadJob, status: str) -> None:
        try:
            await asyncio.to_thread(self.repository.record, job.user_id, job.url, status)
        except sqlite3.Error:
            logger.exception("Could not record download status %s", status)

    async def _save_cache(self, url: str, cached: CachedDownload) -> None:
        try:
            await asyncio.to_thread(self.repository.save_cached, url, cached)
        except sqlite3.Error:
            logger.exception("Could not store media cache entry")

    @staticmethod
    def _progress_text(percent: int) -> str:
        filled = percent // 5
        bar = "█" * filled + "░" * (20 - filled)
        return f"در حال دانلود...\n\n{bar} {percent}%\nلطفاً صبر کنید."

    async def _send_result(self, user_id: int, result: DownloadResult) -> list[CachedMedia]:
        if len(result.files) == 1:
            item = result.files[0]
            if item.kind is MediaKind.VIDEO:
                caption_key = save_caption(
                    self._shorten_caption(self._with_bot_username(result.caption or result.title))
                )
                sent = await self.bot.send_video(
                    user_id,
                    FSInputFile(item.path),
                    reply_markup=self._video_keyboard(caption_key),
                )
                if not sent.video:
                    raise RuntimeError("Telegram response did not include the uploaded video.")
                return [CachedMedia(sent.video.file_id, MediaKind.VIDEO)]
            else:
                sent = await self.bot.send_photo(
                    user_id,
                    FSInputFile(item.path),
                    caption=self._with_bot_username(result.title, 900),
                )
                if not sent.photo:
                    raise RuntimeError("Telegram response did not include the uploaded photo.")
                return [CachedMedia(sent.photo[-1].file_id, MediaKind.IMAGE)]
        # Telegram media groups are limited to 10 items; send larger carousels in chunks.
        cached_files: list[CachedMedia] = []
        for start, end in self._media_group_ranges(len(result.files)):
            group = result.files[start:end]
            media = []
            for index, item in enumerate(group):
                if item.kind is MediaKind.VIDEO:
                    media.append(InputMediaVideo(media=FSInputFile(item.path)))
                else:
                    media.append(
                        InputMediaPhoto(
                            media=FSInputFile(item.path),
                            caption=self._with_bot_username(result.title, 900)
                            if start == 0 and index == 0
                            else None,
                        )
                    )
            sent_messages = await self.bot.send_media_group(user_id, media=media)
            cached_files.extend(self._cached_media(message) for message in sent_messages)
        if any(item.kind is MediaKind.VIDEO for item in result.files):
            await self._send_caption_button(user_id, result.caption or result.title)
        if len(cached_files) != len(result.files):
            raise RuntimeError("Telegram returned an incomplete media group response.")
        return cached_files

    async def _send_cached(self, user_id: int, cached: CachedDownload) -> None:
        if len(cached.files) == 1:
            item = cached.files[0]
            if item.kind is MediaKind.VIDEO:
                caption_key = save_caption(
                    self._shorten_caption(self._with_bot_username(cached.caption or cached.title))
                )
                await self.bot.send_video(
                    user_id,
                    item.file_id,
                    reply_markup=self._video_keyboard(caption_key),
                )
            else:
                await self.bot.send_photo(
                    user_id,
                    item.file_id,
                    caption=self._with_bot_username(cached.title, 900),
                )
            return

        for start, end in self._media_group_ranges(len(cached.files)):
            group = cached.files[start:end]
            media = []
            for index, item in enumerate(group):
                caption = (
                    self._with_bot_username(cached.title, 900)
                    if start == 0 and index == 0 and item.kind is MediaKind.IMAGE
                    else None
                )
                if item.kind is MediaKind.VIDEO:
                    media.append(InputMediaVideo(media=item.file_id, caption=caption))
                else:
                    media.append(InputMediaPhoto(media=item.file_id, caption=caption))
            await self.bot.send_media_group(user_id, media=media)
        if any(item.kind is MediaKind.VIDEO for item in cached.files):
            await self._send_caption_button(user_id, cached.caption or cached.title)

    @staticmethod
    def _cached_media(message: Message) -> CachedMedia:
        if message.video:
            return CachedMedia(message.video.file_id, MediaKind.VIDEO)
        if message.photo:
            return CachedMedia(message.photo[-1].file_id, MediaKind.IMAGE)
        raise RuntimeError("Telegram response did not contain a supported media file.")

    @staticmethod
    def _media_group_ranges(count: int) -> Iterator[tuple[int, int]]:
        start = 0
        while start < count:
            size = min(10, count - start)
            if count - start - size == 1:
                size -= 1
            yield start, start + size
            start += size

    async def _send_caption_button(self, user_id: int, caption: str) -> None:
        key = save_caption(self._shorten_caption(self._with_bot_username(caption)))
        await self.bot.send_message(
            user_id,
            "برای دیدن کپشن کلیپ روی دکمه زیر بزن:",
            reply_markup=self._caption_keyboard(key),
        )

    @staticmethod
    def _caption_keyboard(key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📝 نمایش کپشن",
                        callback_data=f"caption:{key}",
                        style="success",
                    )
                ]
            ]
        )

    @staticmethod
    def _video_keyboard(key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📝 نمایش کپشن",
                        callback_data=f"caption:{key}",
                        style="success",
                    ),
                    InlineKeyboardButton(
                        text="🎙 تبدیل به ویس",
                        callback_data="convert_to_voice",
                        style="primary",
                    ),
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

    @staticmethod
    def _with_bot_username(caption: str, limit: int = MAX_TELEGRAM_CAPTION_LENGTH) -> str:
        suffix = f"\n\n{BOT_USERNAME}"
        caption = caption.strip()
        if len(caption) + len(suffix) <= limit:
            return caption + suffix
        available = max(0, limit - len(suffix))
        return caption[:available].rstrip() + suffix
