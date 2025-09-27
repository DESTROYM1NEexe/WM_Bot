# bot.py
import logging
import re
import uuid

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ========== НАСТРОЙКИ - ЗАМЕНИ НА СВОИ ==========
API_TOKEN = "7706883731:AAFhaYepMiGqBGv9ukG7OabIafyZ7WNPC0g"    # токен от BotFather
CHANNEL_ID = -1002328964343          # id канала, куда публиковать (целое отрицательное для публичных каналов)
MOD_CHAT_ID = -1002726262070        # id приватного чата/группы модерации (только админы)
ADMINS = [6383171904]      # список id телеграм-пользователей-админов (целые числа)
# ==============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# Вставь в свой бот (aiogram v2)
@dp.message_handler(commands=["id"])
async def cmd_id(message: types.Message):
    # возвращаем ID чата и ID пользователя, который вызвал команду
    await message.reply(f"chat_id: {message.chat.id}\nuser_id: {message.from_user.id}")

# В памяти храним посты на модерации: id -> dict
pending_posts = {}

# FSM состояния
class SellForm(StatesGroup):
    photos = State()
    price = State()
    condition = State()
    description = State()
    size = State()
    contact = State()
    city = State()

# /start
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📦 Разместить объявление", "ℹ️ Инфо")
    await message.answer(
        "Привет! 👋 Это бот WM.\n"
        "Хочешь разместить объявление в канале?\n"
        "Выберите действие:",
        reply_markup=kb,
    )

# /info
@dp.message_handler(commands=["info"])
async def cmd_info(message: types.Message):
    text = (
        "📌 Правила размещения (обязательны):\n\n"
        "• Фото — от 1 до 5 шт. (чёткие)\n"
        "• Цена — финальная стоимость (например: 2 990)\n"
        "• Состояние — новое / б/у\n"
        "• Описание и размер в одной строке: "
        "например: `Футболка Represent размер 46`\n"
        "• Контакт — обязательно в формате `@username` или ссылка на t.me/username\n"
        "• Город — опционально\n\n"
        "🔸 Мы не размещаем ссылки на телеграм-каналы/магазины.\n"
        "🚨 Размещение — бесплатно."
    )
    await message.answer(text, parse_mode="Markdown")

# Начать размещение
@dp.message_handler(lambda m: m.text == "📦 Разместить объявление")
@dp.message_handler(commands=["sell"])
async def cmd_sell(message: types.Message):
    await SellForm.photos.set()
    await message.answer("Отправь фото товара (от 1 до 5). Когда закончишь — напиши `готово`.")

# Приём фото (1..5)
@dp.message_handler(content_types=["photo"], state=SellForm.photos)
async def process_photos(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(message.photo[-1].file_id)
    if len(photos) > 5:
        await message.answer("Можно загрузить максимум 5 фото ❗")
        return
    await state.update_data(photos=photos)
    await message.answer(f"Фото приняты ({len(photos)}/5). Пришли ещё или напиши `готово`.")

@dp.message_handler(lambda m: m.text and m.text.lower().strip() == "готово", state=SellForm.photos)
async def finish_photos(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("photos"):
        await message.answer("Ты не отправил ни одного фото. Отправь фото или нажми /cancel.")
        return
    await SellForm.next()
    await message.answer("Укажи цену.")

# Цена
@dp.message_handler(state=SellForm.price)
async def process_price(message: types.Message, state: FSMContext):
    price = message.text.strip()
    # Небольшая нормализация: заменить мн. пробелы на один
    price = re.sub(r"\s+", " ", price)
    await state.update_data(price=price)
    await SellForm.next()
    await message.answer("Состояние (новое / б/у).")

# Состояние
@dp.message_handler(state=SellForm.condition)
async def process_condition(message: types.Message, state: FSMContext):
    cond = message.text.strip().lower()
    await state.update_data(condition=cond)
    await SellForm.next()
    await message.answer("Описание (пример: Футболка размер: x).")

# Описание (включая тип и модель)
@dp.message_handler(state=SellForm.description)
async def process_description(message: types.Message, state: FSMContext):
    desc = message.text.strip()
    await state.update_data(description=desc)
    await SellForm.next()
    await message.answer("Размер")

# Размер (опционально)
@dp.message_handler(state=SellForm.size)
async def process_size(message: types.Message, state: FSMContext):
    size = message.text.strip()
    await state.update_data(size=size)
    await SellForm.next()
    await message.answer("Контакт (обязательно @username или t.me/username).")

# Контакт — нормализация в @username
def normalize_contact(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    # если это ссылка t.me/...
    m = re.search(r"(?:t\.me/|telegram\.me/)(@?[\w\d_]+)", raw)
    if m:
        username = m.group(1)
        if not username.startswith("@"):
            username = "@" + username
        return username
    # если просто username без @
    if re.fullmatch(r"[\w\d_]+", raw):
        return "@" + raw
    # если уже начинается с @username
    if raw.startswith("@") and re.fullmatch(r"@[\w\d_]+", raw):
        return raw
    # иначе вернуть raw (как есть) — но лучше попросить пользователя исправить
    return raw

@dp.message_handler(state=SellForm.contact)
async def process_contact(message: types.Message, state: FSMContext):
    contact_raw = message.text.strip()
    contact = normalize_contact(contact_raw)
    if not contact or not contact.startswith("@"):
        await message.answer(
            "Контакт должен содержать телеграм-юзернейм в формате `@username` или ссылку на t.me/username.\n"
            "Пожалуйста, отправь контакт повторно."
        )
        return
    await state.update_data(contact=contact)
    await SellForm.next()
    await message.answer("Укажи город (или напиши 'нет').")

# Город + формируем пост и отправляем в модерацию
@dp.message_handler(state=SellForm.city)
async def process_city(message: types.Message, state: FSMContext):
    city = message.text.strip()
    await state.update_data(city=city)
    data = await state.get_data()

    # Формируем строку описания: если size указан отдельно, добавляем
    desc_line = data["description"]
    if data.get("size") and data["size"].lower() != "нет":
        # если пользователь ввёл размер отдельно и не указал его в описании
        if "размер" not in desc_line.lower():
            desc_line = f"{desc_line} размер {data['size']}"

    caption = (
        f"Цена:{data['price']}\n"
        f"Состояние:{data['condition']}\n"
        f"Описание:{desc_line}\n"
        f"Купить:{data['contact']}"
    )
    if city and city.lower() != "нет":
        caption += f"\nГород:{city}"

    # Соберём media group
    media = []
    for i, file_id in enumerate(data["photos"]):
        if i == 0:
            media.append(types.InputMediaPhoto(file_id, caption=caption))
        else:
            media.append(types.InputMediaPhoto(file_id))

    # Сохраняем временно пост (uuid -> данные)
    post_id = str(uuid.uuid4())
    pending_posts[post_id] = {
        "user_id": message.from_user.id,
        "photos": data["photos"],
        "caption": caption,
    }

    # Отправляем медиа в чат модерации и сохраняем id отправленного сообщения
    sent = await bot.send_media_group(MOD_CHAT_ID, media)
    # sent — список сообщений; берем id первого
    moderator_msg_id = sent[0].message_id

    # Клавиатура для модераторов (callback содержит post_id)
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{post_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{post_id}")
    )

    await bot.send_message(MOD_CHAT_ID, f"ℹ️ Новое объявление на модерации (id: {post_id})", reply_markup=kb)

    await message.answer("✅ Твоё объявление отправлено на модерацию. Ожидай решения модераторов.")
    await state.finish()

# Обработчик нажатий модераторов
@dp.callback_query_handler(lambda c: c.data and (c.data.startswith("approve:") or c.data.startswith("reject:")))
async def process_moderation(callback: types.CallbackQuery):
    data = callback.data
    admin_id = callback.from_user.id

    # только админы могут модерать
    if admin_id not in ADMINS:
        await callback.answer("У тебя нет прав модератора", show_alert=True)
        return

    action, post_id = data.split(":", 1)
    post = pending_posts.get(post_id)
    if not post:
        await callback.answer("Пост не найден или уже обработан", show_alert=True)
        return

    # Одобрение: публикуем в CHANNEL_ID
    if action == "approve":
        # публикуем media_group в канал
        media = []
        for i, file_id in enumerate(post["photos"]):
            if i == 0:
                media.append(types.InputMediaPhoto(file_id, caption=post["caption"]))
            else:
                media.append(types.InputMediaPhoto(file_id))
        await bot.send_media_group(CHANNEL_ID, media)
        await callback.answer("Объявление опубликовано ✅")
        # уведомляем автора
        try:
            await bot.send_message(post["user_id"], "Ваше объявление было одобрено и опубликовано. Спасибо!")
        except Exception:
            pass

    else:  # reject
        await callback.answer("Объявление отклонено ❌")
        try:
            await bot.send_message(post["user_id"], "Ваше объявление было отклонено модератором.")
        except Exception:
            pass

    # удаляем из pending
    pending_posts.pop(post_id, None)

# Команда /cancel чтобы прервать FSM
@dp.message_handler(commands=["cancel"], state="*")
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Действие отменено.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
