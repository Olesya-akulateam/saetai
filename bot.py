import asyncio
import os
import re
import logging
from typing import Optional, List
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from analyzer import analyze_instagram

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
_allowed_raw = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = set(int(x.strip()) for x in _allowed_raw.split(",") if x.strip()) if _allowed_raw else set()

bot: Bot = None  # type: ignore
dp = Dispatcher(storage=MemoryStorage())


class S(StatesGroup):
    waiting_nick = State()


def extract_nick(text: str) -> Optional[str]:
    text = text.strip()
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", text)
    if m:
        return m.group(1).rstrip("/")
    if text.startswith("@"):
        nick = text[1:]
        if re.match(r"^[A-Za-z0-9._]{1,30}$", nick):
            return nick
    if re.match(r"^[A-Za-z0-9._]{1,30}$", text):
        return text
    return None


def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def split_message(text: str, max_len: int = 4000) -> List[str]:
    chunks = []
    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def run_analysis(message: types.Message, nick: str):
    status_msg = await message.answer(f"⏳ Анализирую <b>@{nick}</b>...", parse_mode="HTML")

    async def on_progress(text: str):
        try:
            await bot.edit_message_text(
                text=f"⏳ <b>@{nick}</b>\n\n{text}",
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                parse_mode="HTML"
            )
        except Exception:
            pass

    try:
        report = await analyze_instagram(nick, progress_callback=on_progress)
        await bot.delete_message(status_msg.chat.id, status_msg.message_id)

        chunks = split_message(report)
        for i, chunk in enumerate(chunks):
            await message.answer(chunk, parse_mode="HTML")
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)

        log.info("Analysis done for @%s, %d chars, %d chunks", nick, len(report), len(chunks))

    except Exception as e:
        log.exception("Error analyzing @%s", nick)
        await bot.edit_message_text(
            f"❌ Ошибка при анализе @{nick}: {e}",
            chat_id=status_msg.chat.id,
            message_id=status_msg.message_id
        )


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.set_state(S.waiting_nick)
    await message.answer(
        "👋 Привет! Пришли @ник или ссылку на Instagram-профиль.\n\n"
        "Я проанализирую упаковку, найду конкурентов и предложу варианты автоматизации с кодовыми словами.\n\n"
        "<i>Пример: <code>@olesya_saet</code></i>",
        parse_mode="HTML"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_allowed(message.from_user.id):
        return
    await message.answer(
        "<b>Как пользоваться:</b>\n\n"
        "Отправь @ник или ссылку на Instagram — получишь:\n"
        "• Оценку упаковки профиля\n"
        "• Анализ конкурентов в нише\n"
        "• Новую шапку и структуру хайлайтов\n"
        "• 5+ вариантов автоматизации с кодовыми словами и готовыми текстами",
        parse_mode="HTML"
    )


@dp.message(S.waiting_nick)
async def handle_nick(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return

    nick = extract_nick(message.text or "")
    if not nick:
        await message.answer(
            "Не распознал ник. Пришли @ник или ссылку:\n<code>instagram.com/ник</code>",
            parse_mode="HTML"
        )
        return

    await run_analysis(message, nick)
    await state.set_state(S.waiting_nick)


@dp.message()
async def handle_any(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    current = await state.get_state()
    if current is None:
        await state.set_state(S.waiting_nick)
    await handle_nick(message, state)


async def main():
    global bot
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в .env")
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан в .env")

    bot = Bot(token=BOT_TOKEN)
    log.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
