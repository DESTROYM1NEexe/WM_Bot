import asyncio
import logging
import os
import re
import uuid
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaPhoto,
                           KeyboardButton, Message, ReplyKeyboardMarkup)
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
if API_TOKEN is None:
    raise ValueError("BOT_TOKEN not set in environment variables")

channel_id_str = os.getenv("CHANNEL_ID")
if channel_id_str is None:
    raise ValueError("CHANNEL_ID not set in environment variables")
try:
    CHANNEL_ID = int(channel_id_str)
except ValueError:
    raise ValueError("CHANNEL_ID must be an integer")

mod_chat_id_str = os.getenv("MOD_CHAT_ID")
if mod_chat_id_str is None:
    raise ValueError("MOD_CHAT_ID not set in environment variables")
try:
    MOD_CHAT_ID = int(mod_chat_id_str)
except ValueError:
    raise ValueError("MOD_CHAT_ID must be an integer")

admins_str = os.getenv("ADMINS", "")
ADMINS = []
if admins_str:
    try:
        ADMINS = [int(x) for x in admins_str.split(",") if x]
    except ValueError:
        raise ValueError("ADMINS must be comma-separated integers")

logging.basicConfig(level=logging.INFO)

# FSM
class SellForm(StatesGroup):
    photos = State()
    price = State()
    condition = State()
    description = State()
    size = State()
    contact = State()
    city = State()

# Память для постов на модерации
pending_posts: Dict[str, dict] = {}

# Бот и диспетчер
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ===== Команды =====
@dp.message(Command("id"))
async def cmd_id(message: Message):
    if message.from_user is None:
        logging.warning("Message without from_user")
        return
    await message.answer(f"chat_id: {message.chat.id}\nuser_id: {message.from_user.id}")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Разместить объявление")],
            [KeyboardButton(text="ℹ️ Инфо")],
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "Привет! 👋 Это бот WM.\n"
        "Хочешь разместить объявление в канале?\n"
        "Выберите действие:",
        reply_markup=kb,
    )

@dp.message(Command("info"))
async def cmd_info(message: Message):
    text = (
        "📌 Правила размещения:\n\n"
        "• Фото — 1-5 шт.\n"
        "• Цена — финальная стоимость\n"
        "• Состояние — новое / б/у\n"
        "• Описание + размер в одной строке\n"
        "• Контакт — @username или t.me/username\n"
        "• Город — опционально\n\n"
        "🔸 Мы не размещаем ссылки на каналы/магазины.\n"
        "🚨 Размещение бесплатно."
    )
    await message.answer(text)

# ===== Логика формы =====
@dp.message(F.text == "📦 Разместить объявление")
@dp.message(Command("sell"))
async def cmd_sell(message: Message, state: FSMContext):
    await state.set_state(SellForm.photos)
    await message.answer("Отправь фото товара (1-5). Когда закончишь — напиши `готово`.")

@dp.message(SellForm.photos, F.photo)
async def process_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos: List[str] = data.get("photos", [])
    if message.photo and len(message.photo) > 0:
        photos.append(message.photo[-1].file_id)
    else:
        await message.answer("Это не фото. Отправь фото.")
        return
    if len(photos) > 5:
        await message.answer("Можно максимум 5 фото ❗")
        return
    await state.update_data(photos=photos)
    await message.answer(f"Фото приняты ({len(photos)}/5). Пришли ещё или напиши `готово`.")

@dp.message(SellForm.photos, F.text.lower() == "готово")
async def finish_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("photos"):
        await message.answer("Ты не отправил ни одного фото. Отправь фото или нажми /cancel.")
        return
    await state.set_state(SellForm.price)
    await message.answer("Укажи цену.")

@dp.message(SellForm.price)
async def process_price(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, укажи цену текстом.")
        return
    price = re.sub(r"\s+", " ", message.text.strip())
    if not price:
        await message.answer("Цена не может быть пустой. Укажи цену.")
        return
    await state.update_data(price=price)
    await state.set_state(SellForm.condition)
    await message.answer("Состояние (новое / б/у).")

@dp.message(SellForm.condition)
async def process_condition(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, укажи состояние текстом.")
        return
    condition = message.text.strip()
    if not condition:
        await message.answer("Состояние не может быть пустым. Укажи состояние.")
        return
    await state.update_data(condition=condition)
    await state.set_state(SellForm.description)
    await message.answer("Описание.")

@dp.message(SellForm.description)
async def process_description(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, укажи описание текстом.")
        return
    description = message.text.strip()
    if not description:
        await message.answer("Описание не может быть пустым. Укажи описание.")
        return
    await state.update_data(description=description)
    await state.set_state(SellForm.size)
    await message.answer("Размер (или 'нет').")

@dp.message(SellForm.size)
async def process_size(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, укажи размер текстом.")
        return
    size = message.text.strip()
    await state.update_data(size=size)
    await state.set_state(SellForm.contact)
    await message.answer("Контакт (@username или t.me/username).")

def normalize_contact(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    m = re.search(r"(?:t\.me/|telegram\.me/)(@?[\w\d_]+)", raw, re.IGNORECASE)
    if m:
        username = m.group(1)
        if not username.startswith("@"):
            username = "@" + username
        return username
    if re.fullmatch(r"[\w\d_]+", raw, re.IGNORECASE):
        return "@" + raw
    if raw.startswith("@") and re.fullmatch(r"@[\w\d_]+", raw, re.IGNORECASE):
        return raw
    return ""

@dp.message(SellForm.contact)
async def process_contact(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, укажи контакт текстом.")
        return
    contact = normalize_contact(message.text)
    if not contact or not contact.startswith("@"):
        await message.answer(
            "Контакт должен быть в формате @username или ссылка t.me/username.\n"
            "Отправь повторно."
        )
        return
    await state.update_data(contact=contact)
    await state.set_state(SellForm.city)
    await message.answer("Укажи город (или 'нет').")

@dp.message(SellForm.city)
async def process_city(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, укажи город текстом.")
        return
    city = message.text.strip()
    await state.update_data(city=city)
    data = await state.get_data()

    desc_line = data.get("description", "")
    size = data.get("size", "")
    if size and size.lower() != "нет":
        if "размер" not in desc_line.lower():
            desc_line = f"{desc_line} размер {size}".strip()

    caption = (
        f"Цена: {data.get('price', '')}\n"
        f"Состояние: {data.get('condition', '')}\n"
        f"Описание: {desc_line}\n"
        f"Купить: {data.get('contact', '')}"
    )
    if city and city.lower() != "нет":
        caption += f"\nГород: {city}"

    photos = data.get("photos", [])
    if not photos:
        await message.answer("Нет фото. Отмена.")
        await state.clear()
        return

    # Define media list explicitly to avoid undefined variable errors
    media: List[InputMediaPhoto] = []
    for i, file_id in enumerate(photos):
        media.append(InputMediaPhoto(media=file_id, caption=caption if i == 0 else None))

    post_id = str(uuid.uuid4())
    pending_posts[post_id] = {
        "user_id": message.from_user.id if message.from_user else 0,
        "photos": photos,
        "caption": caption,
    }

    try:
        sent = await bot.send_media_group(chat_id=MOD_CHAT_ID, media=media) # type: ignore
        if not sent:
            raise ValueError("No messages sent")
        moderator_msg_id = sent[0].message_id

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{post_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{post_id}"),
                ]
            ]
        )

        await bot.send_message(MOD_CHAT_ID, f"ℹ️ Новое объявление (id: {post_id})", reply_markup=kb)
        await message.answer("✅ Твоё объявление отправлено на модерацию.")
    except Exception as e:
        logging.error(f"Error sending to mod chat: {e}")
        await message.answer("Ошибка при отправке на модерацию. Попробуй позже.")
    finally:
        await state.clear()

@dp.callback_query(F.data.startswith(("approve:", "reject:")))
async def process_moderation(callback: CallbackQuery):
    if callback.from_user is None:
        await callback.answer("No user", show_alert=True)
        return
    if callback.from_user.id not in ADMINS:
        await callback.answer("Нет прав модератора", show_alert=True)
        return
    if callback.data is None:
        await callback.answer("No data", show_alert=True)
        return

    try:
        action, post_id = callback.data.split(":", 1)
    except ValueError:
        await callback.answer("Invalid callback data", show_alert=True)
        return

    post = pending_posts.get(post_id)
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    # Define media list explicitly to avoid undefined variable errors
    media: List[InputMediaPhoto] = []
    for i, file_id in enumerate(post["photos"]):
        media.append(InputMediaPhoto(media=file_id, caption=post["caption"] if i == 0 else None))

    if action == "approve":
        try:
            await bot.send_media_group(chat_id=CHANNEL_ID, media=media) # type: ignore
            await callback.answer("✅ Объявление опубликовано")
            try:
                await bot.send_message(post["user_id"], "Ваше объявление было одобрено ✅")
            except Exception as e:
                logging.error(f"Error notifying user: {e}")
        except Exception as e:
            logging.error(f"Error publishing: {e}")
            await callback.answer("Ошибка при публикации", show_alert=True)
    else:
        await callback.answer("❌ Отклонено")
        try:
            await bot.send_message(post["user_id"], "Ваше объявление было отклонено ❌")
        except Exception as e:
            logging.error(f"Error notifying user: {e}")

    pending_posts.pop(post_id, None)

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.")

async def main():
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Polling error: {e}")

if __name__ == "__main__":
    asyncio.run(main())