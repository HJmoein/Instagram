import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.services.media import MediaService
from src.services.captions import get_caption
from src.services.queue import DownloadJob, DownloadQueue
from src.utils.urls import is_instagram_url

logger = logging.getLogger(__name__)
router = Router(name="media")

ABOUT_TEXT = (
    "معرفی ربات\n\n"
    "با این ربات می‌توانی محتوای عمومی اینستاگرام، از جمله پست، ریلز، عکس، "
    "ویدیو و پست‌های چنداسلایدی را دانلود کنی.\n\n"
    "اگه ضعیفه به کیرم ربات روی سرور قوی نیست که کصکش "
    "خوشت نمیاد برو یه جای دیگه"
)


def video_actions_keyboard(caption_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 نمایش کپشن", callback_data=f"caption:{caption_key}"),
                InlineKeyboardButton(text="🎙 تبدیل به ویس", callback_data="convert_to_voice"),
            ]
        ]
    )


def about_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="بازگشت به شروع", callback_data="about_back")]]
    )


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "سلام، خوش آمدی. امیدوارم از ربات خوشت بیاد.\n\n"
        "لینک عمومی Instagram را بفرست تا در صورت امکان عکس، ویدیو را برایت آماده کنم.\n\n"
        "برای راهنما، /help را ارسال کن یا درباره ربات را ببین.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="درباره ربات", callback_data="about_bot")]]
        ),
    )


@router.message(Command("creator"))
async def creator_handler(message: Message) -> None:
    await message.answer(
        "👤 سازنده ربات: من معین هستم\n\n"
        "این ربات با عشق و زحمت ساخته شده تا استفاده از آن برات راحت باشه\n\n"
        "پس از ربات استفاده کن و لذت ببر نیای بگی این چرا اینطوری اون اینطوری مشکل داری بکیرم  استفاده نکن\n",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 ارتباط با سازنده", url="https://t.me/moein_915")]]
        ),
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "راهنمای ربات\n\n"
        "1. لینک عمومی Post، Reel یا Video را ارسال کن.\n"
        "2. کمی صبر کن تا محتوا بررسی و دانلود شود.\n"
        "3. فایل آماده‌شده در همین گفتگو ارسال می‌شود.\n\n"
        "لینک‌های خصوصی، حذف‌شده یا نیازمند ورود قابل پردازش نیستند."
    )


@router.callback_query(lambda query: query.data and query.data.startswith("caption:"))
async def caption_callback_handler(query: CallbackQuery) -> None:
    await query.answer()
    if not query.message or not query.data:
        return
    caption = get_caption(query.data.removeprefix("caption:"))
    if caption is None:
        if query.message.video:
            await query.message.edit_caption(
                caption="این کپشن دیگر در دسترس نیست.",
                reply_markup=video_actions_keyboard(query.data.removeprefix("caption:")),
            )
        else:
            await query.message.edit_text("این کپشن دیگر در دسترس نیست.")
        return
    if query.message.video:
        await query.message.edit_caption(
            caption=caption[:1024],
            reply_markup=video_actions_keyboard(query.data.removeprefix("caption:")),
        )
    else:
        await query.message.edit_text(caption[:4096])


@router.message(Command("about"))
async def about_command_handler(message: Message) -> None:
    await message.answer(ABOUT_TEXT, reply_markup=about_keyboard())


@router.callback_query(lambda query: query.data in {"about_bot", "about_back"})
async def about_callback_handler(query: CallbackQuery) -> None:
    await query.answer()
    if not query.message:
        return
    if query.data == "about_back":
        await query.message.edit_text(
            "سلام، خوش آمدی. امیدوارم از ربات خوشت بیاد.\n\n"
                "لینک عمومی Instagram را بفرست تا در صورت امکان عکس، ویدیو را برایت آماده کنم.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="درباره ربات", callback_data="about_bot")]]
            ),
        )
        return
    await query.message.edit_text(ABOUT_TEXT, reply_markup=about_keyboard())


def register_media_handler(queue: DownloadQueue, service: MediaService) -> None:
    @router.callback_query(lambda query: query.data == "convert_to_voice")
    async def convert_to_voice_handler(query: CallbackQuery) -> None:
        await query.answer("در حال تبدیل ویدیو به ویس...")
        if not query.message or not query.message.video:
            return
        try:
            await service.convert_video_to_voice(query.message)
        except Exception:
            logger.exception("Failed to convert video to voice")
            await query.message.answer("تبدیل ویدیو به ویس انجام نشد. لطفا دوباره تلاش کن.")

    @router.message()
    async def url_handler(message: Message) -> None:
        url = (message.text or "").strip()
        if not is_instagram_url(url):
            await message.answer(
                "این لینک قابل شناسایی نیست.\n\n"
                "لطفا لینک کامل و عمومی Instagram را ارسال کن؛ مثلا لینک یک Post یا Reel."
            )
            return
        status_sticker = await service.send_download_sticker(message.chat.id)
        job = DownloadJob(
            user_id=message.from_user.id if message.from_user else message.chat.id,
            url=url,
            callback=lambda queued_job: service.process(queued_job, message.chat.id, status_sticker),
        )
        await queue.put(job)
        logger.info("Queued Instagram URL from user %s; queue_size=%s", job.user_id, queue.size())
