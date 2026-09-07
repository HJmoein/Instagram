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
DOWNLOAD_STICKER_PATH = Path(__file__).resolve().parents[2] / "AnimatedSticker.tgs"


class MediaService:
    def __init__(self, bot: Bot, downloader: InstagramDownloader, repository: DownloadRepository) -> None:
        self.bot = bot
        self.downloader = downloader
        self.repository = repository

    async def send_download_sticker(self, chat_id: int):
        if not DOWNLOAD_STICKER_PATH.is_file():
            raise FileNotFoundError(f"Download sticker not found: {DOWNLOAD_STICKER_PATH}")
        return await self.bot.send_sticker(chat_id, FSInputFile(DOWNLOAD_STICKER_PATH))

    async def process(self, job: DownloadJob, chat_id: int, status_sticker=None) -> None:
        try:
            if status_sticker is None:
                status_sticker = await self.send_download_sticker(chat_id)
            result, directory = await self.downloader.download(job.url)
            await self._send_result(job.user_id, result)
            self.repository.record(job.user_id, job.url, "success")
        except DownloadError as exc:
            self.repository.record(job.user_id, job.url, "failed")
            await self._send_error(chat_id, str(exc))
        except TelegramAPIError as exc:
            self.repository.record(job.user_id, job.url, "telegram_failed")
            logger.warning("Telegram rejected media for user %s: %s", job.user_id, exc)
            await self._send_error(
                chat_id,
                "فایل آماده شد، اما Telegram نتوانست آن را ارسال کند.\n"
                "احتمالا حجم یا نوع فایل با محدودیت Telegram سازگار نیست."
            )
        except Exception:
            self.repository.record(job.user_id, job.url, "error")
            logger.exception("Failed to process media job")
            await self._send_error(
                chat_id,
                "یک خطای پیش‌بینی‌نشده رخ داد. لطفا کمی بعد دوباره تلاش کن.",
            )
        finally:
            if status_sticker:
                try:
                    await status_sticker.delete()
                except TelegramAPIError:
                    logger.debug("Could not delete download sticker")
            directory = locals().get("directory")
            if directory:
                shutil.rmtree(directory, ignore_errors=True)

    async def _send_error(self, chat_id: int, text: str) -> None:
        await self.bot.send_message(chat_id, text)

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
