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
    q1 = State()
    q2 = State()
    q3 = State()
    q4 = State()
    q5 = State()


QUESTIONS = [
    (
        "q1",
        "❓ <b>Вопрос 1 из 5</b>\n\n"
        "Что чаще всего пишут вам в директ?\n"
        "<i>Например: вопросы о ценах, как записаться, вопросы о продукте, просто «хочу узнать подробнее»</i>"
    ),
    (
        "q2",
        "❓ <b>Вопрос 2 из 5</b>\n\n"
        "Что вы продаёте или предлагаете в профиле?\n"
        "<i>Кратко: курс, консультации, услуга, товар — и для кого</i>"
    ),
    (
        "q3",
        "❓ <b>Вопрос 3 из 5</b>\n\n"
        "Сколько примерно сообщений приходит в директ в неделю?\n"
        "<i>Хотя бы приблизительно: 5, 20, 50, 100+</i>"
    ),
    (
        "q4",
        "❓ <b>Вопрос 4 из 5</b>\n\n"
        "Уже пробовали автоматизацию или чат-боты?\n"
        "<i>Если да — что понравилось/не понравилось. Если нет — почему не пробовали</i>"
    ),
    (
        "q5",
        "❓ <b>Вопрос 5 из 5</b>\n\n"
        "Какой главный результат хотите получить от автоматизации?\n"
        "<i>Например: экономить время, не терять заявки, продавать пока сплю, отвечать быстрее</i>"
    ),
]

QUESTION_KEYS = ["директ_что_пишут", "продукт", "объём_сообщений", "опыт_автоматизации", "желаемый_результат"]


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


async def run_analysis(message: types.Message, state: FSMContext):
    data = await state.get_data()
    nick = data["nick"]
    answers = {QUESTION_KEYS[i]: data.get(f"answer_{i+1}", "") for i in range(5)}

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
        report = await analyze_instagram(nick, progress_callback=on_progress, qual_answers=answers)
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

    await state.clear()
    await state.set_state(S.waiting_nick)


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.clear()
    await state.set_state(S.waiting_nick)
    await message.answer(
        "👋 Привет! Я анализирую Instagram-профили и подбираю варианты автоматизации.\n\n"
        "Сначала пришли ссылку или @ник профиля, который хочешь разобрать.\n\n"
        "<i>Пример: <code>@olesya_saet</code> или <code>instagram.com/olesya_saet</code></i>",
        parse_mode="HTML"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_allowed(message.from_user.id):
        return
    await message.answer(
        "<b>Как пользоваться:</b>\n\n"
        "1. Отправь @ник или ссылку на Instagram\n"
        "2. Ответь на 5 коротких вопросов\n"
        "3. Подожди 1-2 минуты пока идёт анализ\n"
        "4. Получи отчёт + варианты автоматизации\n\n"
        "<b>Что входит в анализ:</b>\n"
        "• Оценка упаковки профиля\n"
        "• Анализ конкурентов в нише\n"
        "• Рекомендации: шапка, хайлайты, воронка\n"
        "• 5+ вариантов автоматизации под твой профиль\n"
        "• Quick Wins — что сделать прямо сейчас",
        parse_mode="HTML"
    )


@dp.message(Command("restart"))
async def cmd_restart(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.clear()
    await state.set_state(S.waiting_nick)
    await message.answer("🔄 Начинаем заново. Пришли @ник или ссылку на Instagram.", parse_mode="HTML")


@dp.message(S.waiting_nick)
async def handle_nick(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return

    nick = extract_nick(message.text or "")
    if not nick:
        await message.answer(
            "Не распознал ник. Пришли @ник или ссылку вида:\n"
            "<code>instagram.com/ник</code>",
            parse_mode="HTML"
        )
        return

    await state.update_data(nick=nick)
    await state.set_state(S.q1)
    await message.answer(
        f"✅ Профиль: <b>@{nick}</b>\n\n"
        f"Теперь задам 5 коротких вопросов — чтобы подобрать варианты автоматизации именно под твою ситуацию.\n\n"
        f"{QUESTIONS[0][1]}",
        parse_mode="HTML"
    )


@dp.message(S.q1)
async def handle_q1(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.update_data(answer_1=message.text)
    await state.set_state(S.q2)
    await message.answer(QUESTIONS[1][1], parse_mode="HTML")


@dp.message(S.q2)
async def handle_q2(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.update_data(answer_2=message.text)
    await state.set_state(S.q3)
    await message.answer(QUESTIONS[2][1], parse_mode="HTML")


@dp.message(S.q3)
async def handle_q3(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.update_data(answer_3=message.text)
    await state.set_state(S.q4)
    await message.answer(QUESTIONS[3][1], parse_mode="HTML")


@dp.message(S.q4)
async def handle_q4(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.update_data(answer_4=message.text)
    await state.set_state(S.q5)
    await message.answer(QUESTIONS[4][1], parse_mode="HTML")


@dp.message(S.q5)
async def handle_q5(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        return
    await state.update_data(answer_5=message.text)
    await message.answer(
        "✅ <b>Отлично, всё понял!</b>\n\n"
        "Теперь анализирую профиль и подбираю варианты воронок под твою ситуацию.\n"
        "Это займёт 1-2 минуты ⏳",
        parse_mode="HTML"
    )
    await run_analysis(message, state)


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
