import asyncio
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaAudio,
                           InputMediaDocument, InputMediaPhoto,
                           InputMediaVideo, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

# ---------------- CONFIG ----------------
# Замените на свои значения или используйте .env + python-dotenv
API_TOKEN = "8477337530:AAHjoB6-Ve_bd-qDd-Uc-C4TXikkKMt3H7A"
CHANNEL_ID = -1002328964343  # канал публикации
MODER_CHAT_ID = -1002726262070  # чат модераторов
ADMINS: List[int] = [6383171904]  # сюда ID владельца(ей)/админов, которые должны видеть уведомления

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------- FSM ----------------
class PostForm(StatesGroup):
    photos = State()
    price = State()
    condition = State()
    description = State()
    contact = State()
    city = State()
    # внутренние состояния для модерации (если потребуется)
    waiting_reason = State()

# ---------------- CallbackData ----------------
class ModerCallback(CallbackData, prefix="moder"):
    action: str
    post_id: str

# ---------------- bot / dp ----------------
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ---------------- data storages ----------------
# pending_posts: post_id -> data about post (author, photos, caption, mod_msg_id)
pending_posts: Dict[str, Dict[str, Any]] = {}
# awaiting_reasons: prompt_message_id -> {"post_id": str, "admin_id": int, "mod_msg_id": int}
awaiting_reasons: Dict[int, Dict[str, Any]] = {}

# lock file to avoid double-run locally
lock_file = Path("bot.lock")
if lock_file.exists():
    logging.warning("Файл bot.lock найден — удаляю и продолжаю (локальный dev режим).")
    try:
        lock_file.unlink(missing_ok=True)
    except Exception:
        pass
lock_file.touch()

# ---------------- helpers ----------------
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def kb_main() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="📦 Разместить объявление"), KeyboardButton(text="ℹ️ Инфо")],
        [KeyboardButton(text="❌ Отмена")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def kb_moder(post_id: str) -> InlineKeyboardMarkup:
    inline_keyboard = [
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=ModerCallback(action="approve", post_id=post_id).pack()),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=ModerCallback(action="reject", post_id=post_id).pack()),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

def normalize_contact(raw: Optional[str]) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("@"):
        return raw
    # допустимы t.me/username и telegram.me/username
    m = re.search(r"(?:t\.me/|telegram\.me/)(@?[\w\d_]+)", raw, re.IGNORECASE)
    if m:
        u = m.group(1)
        return u if u.startswith("@") else "@" + u
    # если просто username без @
    if re.fullmatch(r"[\w\d_]+", raw):
        return "@" + raw
    return raw

# ---------------- sanity check API token (optional) ----------------
def check_token() -> None:
    try:
        r = requests.get(f"https://api.telegram.org/bot{API_TOKEN}/getMe", timeout=10)
        if r.status_code != 200:
            logging.error("Ошибка авторизации бота (getMe): %s", r.text)
            # не выходим, но логируем
        else:
            logging.info("Bot authorized OK")
    except Exception as e:
        logging.warning("Не удалось проверить токен: %s", e)

check_token()

# ---------------- Handlers (user flow) ----------------
@router.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.answer("Привет! 👋 Это бот для публикации объявлений. Нажми кнопку ниже.", reply_markup=kb_main())

@router.message(F.text == "ℹ️ Инфо")
async def cmd_info(msg: Message):
    await msg.answer(
        "📌 Укажите в объявлении:\n"
        "•Фото товара — чёткие, с хорошим светом\n"
        "•Цена — укажите финальную стоимость\n"
        "•Состояние — новое / б/у\n"
        "•Описание — кратко (max 700 символов)\n"
        "•Контакт — только @username (обязательно)\n"
        "• Город (необязательно)\n",
        reply_markup=kb_main()
    )

@router.message(F.text == "📦 Разместить объявление")
async def cmd_sell(msg: Message, state: FSMContext):
    await state.set_state(PostForm.photos)
    await state.update_data(photos=[])
    await msg.answer("Отправь фото товара (1–5). Когда закончишь — напиши 'готово'.", reply_markup=kb_main())

@router.message(F.text == "❌ Отмена")
async def cmd_cancel_text(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Действие отменено.", reply_markup=kb_main())

@router.message(F.photo, PostForm.photos)
async def handler_photo(msg: Message, state: FSMContext):
    if not msg.photo:
        await msg.answer("Это не фото, отправьте изображение.")
        return
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 5:
        await msg.answer("Максимум 5 фото. Напишите 'готово' для продолжения.")
        return
    photos.append(msg.photo[-1].file_id)
    await state.update_data(photos=photos)
    await msg.answer(f"Фото принято ({len(photos)}/5). Ещё или 'готово'?", reply_markup=kb_main())

@router.message(PostForm.photos, F.text)
async def handle_text_in_photos(msg: Message, state: FSMContext):
    text = (msg.text or "").lower().strip()
    if text == "готово":
        data = await state.get_data()
        photos = data.get("photos", [])
        if not photos:
            await msg.answer("Нужно хотя бы одно фото.", reply_markup=kb_main())
            return
        await state.set_state(PostForm.price)
        await msg.answer("Укажи цену (например: 2 990).", reply_markup=kb_main())
    else:
        await msg.answer("Отправьте фото или напишите 'готово'.")

@router.message(PostForm.price)
async def handler_price(msg: Message, state: FSMContext):
    text = (msg.text or "").strip()
    if not text:
        await msg.answer("Цена не может быть пустой. Введите цену (например: 2 990 или 'договорная').")
        return
    await state.update_data(price=text)
    await state.set_state(PostForm.condition)
    await msg.answer("Состояние (новое / б/у).", reply_markup=kb_main())

@router.message(PostForm.condition)
async def handler_condition(msg: Message, state: FSMContext):
    text = (msg.text or "").strip()
    if not text:
        await msg.answer("Укажите состояние (например: новое или б/у).")
        return
    await state.update_data(condition=text)
    await state.set_state(PostForm.description)
    await msg.answer("Описание товара (коротко, max 700 символов).", reply_markup=kb_main())

@router.message(PostForm.description)
async def handler_description(msg: Message, state: FSMContext):
    text = (msg.text or "").strip()
    if not text:
        await msg.answer("Описание не может быть пустым. Введите описание.")
        return
    if len(text) > 700:
        await msg.answer("Описание слишком длинное (max 700 символов). Укоротите и отправьте снова.")
        return
    await state.update_data(description=text)
    await state.set_state(PostForm.contact)
    await msg.answer("Контакт — обязательно @username (например: @ivan).", reply_markup=kb_main())

@router.message(PostForm.contact)
async def handler_contact(msg: Message, state: FSMContext):
    raw = (msg.text or "").strip()
    contact = normalize_contact(raw)
    if not contact.startswith("@"):
        await msg.answer("Контакт обязателен и должен начинаться с @. Введите снова (только @username).")
        return
    if not re.match(r'^@[a-zA-Z\d_]{5,32}$', contact, re.IGNORECASE):
        await msg.answer("Неверный формат @username (5-32 символа: буквы, цифры, _). Введите снова.")
        return
    await state.update_data(contact=contact)
    await state.set_state(PostForm.city)
    await msg.answer("Город (необязательно). Напишите 'нет', если не указывать.", reply_markup=kb_main())

@router.message(PostForm.city)
async def handler_city(msg: Message, state: FSMContext):
    city_text = (msg.text or "").strip()
    if not city_text:
        city_text = "нет"
    await state.update_data(city=city_text)

    data = await state.get_data()
    photos: List[str] = data.get("photos", [])
    if not photos:
        await msg.answer("Ошибка: фото не найдены. Начните заново.", reply_markup=kb_main())
        await state.clear()
        return

    # Формируем подпись (caption)
    caption_lines = [
        f"Цена: {data.get('price','')}",
        f"Состояние: {data.get('condition','')}",
        f"Описание: {data.get('description','')}",
        f"Контакт: {data.get('contact','')}"
    ]
    if city_text.lower() != "нет":
        caption_lines.append(f"📍 Город: {city_text}")
    caption = "\n".join(caption_lines)
    if len(caption) > 1024:
        await msg.answer("Общая подпись слишком длинная. Укоротите описание или другие поля и попробуйте заново.")
        await state.set_state(PostForm.description)
        return

    # Формируем media для отправки в модеральный чат
    media: List[Union[InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio]] = []
    for i, fid in enumerate(photos):
        media.append(InputMediaPhoto(media=fid, caption=caption if i == 0 else None))

    # создаём уникальный id поста
    post_id = str(uuid.uuid4())
    author = msg.from_user
    author_id = author.id if author else 0
    author_name = author.full_name if author else "unknown"
    author_username = ("@" + author.username) if (author and author.username) else "нет"

    # отправляем media_group в мод.чат
    try:
        sent = await bot.send_media_group(chat_id=MODER_CHAT_ID, media=media)
    except Exception as e:
        logging.exception("Не удалось отправить media_group в мод.чат: %s", e)
        await msg.answer("Ошибка отправки на модерацию. Попробуйте позже.", reply_markup=kb_main())
        await state.clear()
        return

    # Отправляем текст с кнопками (чтобы callback привязан к этому сообщению)
    moder_text = (
        f"<b>Новое объявление (id: {post_id})</b>\n\n"
        f"{caption}\n\n"
        f"👤 От: <a href='tg://user?id={author_id}'>{author_name}</a>\n"
        f"🆔 ID: <code>{author_id}</code>\n"
        f"🔗 Username: {author_username}\n"
        f"🕒 Время: {now_str()}"
    )
    try:
        mod_msg = await bot.send_message(chat_id=MODER_CHAT_ID, text=moder_text, reply_markup=kb_moder(post_id))
    except Exception as e:
        logging.exception("Не удалось отправить текст модерации: %s", e)
        await msg.answer("Ошибка отправки на модерацию (текст). Попробуйте позже.", reply_markup=kb_main())
        await state.clear()
        return

    # сохраняем пост в памяти: по id сообщения с кнопками
    pending_posts[post_id] = {
        "author_id": author_id,
        "author_name": author_name,
        "author_username": author_username,
        "photos": photos,
        "caption": caption,
        "mod_message_id": mod_msg.message_id,
        "mod_chat_id": MODER_CHAT_ID,
        "time": now_str(),
    }

    await msg.answer("✅ Твоё объявление отправлено на модерацию.", reply_markup=kb_main())
    await state.clear()

# ---------------- Moderation callbacks ----------------
@router.callback_query(ModerCallback.filter(F.action == "approve"))
async def on_approve(cb: CallbackQuery, callback_data: ModerCallback):
    admin = cb.from_user
    if admin is None or admin.id not in ADMINS:
        await cb.answer("У тебя нет прав модератора", show_alert=True)
        return

    post_id = callback_data.post_id
    post = pending_posts.get(post_id)
    if not post:
        await cb.answer("Пост не найден или уже обработан", show_alert=True)
        return

    # Опубликовать в канал
    photos = post.get("photos", [])
    caption = post.get("caption", "")
    media: List[Union[InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio]] = []
    for i, fid in enumerate(photos):
        media.append(InputMediaPhoto(media=fid, caption=caption if i == 0 else None))
    try:
        await bot.send_media_group(chat_id=CHANNEL_ID, media=media)
    except Exception as e:
        logging.exception("Ошибка публикации в канал: %s", e)
        await cb.answer("Ошибка публикации", show_alert=True)
        return

    # уведомляем автора
    try:
        await bot.send_message(chat_id=post["author_id"], text=f"✅ Ваше объявление (ID: {post_id}) одобрено и опубликовано.")
    except Exception:
        logging.debug("Не удалось уведомить автора (возможно, он заблокировал бота).")

    # сообщаем в мод.чат / редактируем сообщение с кнопками
    text_to_edit = f"✅ Опубликовано @{(admin.username or 'нет')} (ID: {admin.id}) — пост id:{post_id}"
    try:
        await bot.edit_message_text(chat_id=MODER_CHAT_ID, message_id=post["mod_message_id"], text=text_to_edit)
    except Exception:
        # если редактирование не получилось — просто отправим сообщение
        await bot.send_message(chat_id=MODER_CHAT_ID, text=text_to_edit)

    # отправим владельцам (ADMINS) лог о том кто опубликовал
    for owner in ADMINS:
        try:
            if owner != admin.id:
                await bot.send_message(owner, f"🔵 Пост {post_id} опубликован модератором @{(admin.username or 'нет')} (id={admin.id}) в {now_str()}")
        except Exception:
            logging.debug("Не удалось отправить уведомление владельцу %s", owner)

    # удаляем из очереди
    pending_posts.pop(post_id, None)
    await cb.answer("Объявление опубликовано")

@router.callback_query(ModerCallback.filter(F.action == "reject"))
async def on_reject(cb: CallbackQuery, callback_data: ModerCallback):
    admin = cb.from_user
    if admin is None or admin.id not in ADMINS:
        await cb.answer("У тебя нет прав модератора", show_alert=True)
        return

    post_id = callback_data.post_id
    post = pending_posts.get(post_id)
    if not post:
        await cb.answer("Пост не найден или уже обработан", show_alert=True)
        return

    # Просим причину — отправляем служебное сообщение и ждём reply
    try:
        prompt = await bot.send_message(chat_id=MODER_CHAT_ID, text="❌ Укажите причину отказа (ответом на это сообщение, max 4000 символов).")
    except Exception as e:
        logging.exception("Ошибка отправки prompt: %s", e)
        await cb.answer("Ошибка при запросе причины", show_alert=True)
        return

    awaiting_reasons[prompt.message_id] = {"post_id": post_id, "admin_id": admin.id, "mod_message_id": post["mod_message_id"]}
    await cb.answer("Напишите причину отказа как ответ на системное сообщение в мод.чате")

# ---------------- Обработка причины (модератор реплаится на prompt) ----------------
@router.message(F.reply_to_message)
async def handle_reason_reply(msg: Message):
    reply_to = msg.reply_to_message
    if not reply_to:
        return
    info = awaiting_reasons.get(reply_to.message_id)
    if not info:
        return  # не наш prompt
    admin = msg.from_user
    if not admin or admin.id != info["admin_id"]:
        await msg.reply("Причину должен указать тот модератор, который запросил отклонение.")
        return
    reason = (msg.text or "").strip()
    if not reason:
        await msg.reply("Причина не может быть пустой.")
        return
    if len(reason) > 4000:
        await msg.reply("Причина слишком длинная (max 4000 символов). Укоротите.")
        return

    post_id = info["post_id"]
    post = pending_posts.get(post_id)
    if not post:
        await msg.reply("Пост уже обработан или не найден.")
        awaiting_reasons.pop(reply_to.message_id, None)
        return

    # уведомляем автора — только причина и кто отказал (id + username)
    moderator_info = f"{admin.full_name} (id={admin.id}, @{admin.username or 'нет'})"
    try:
        await bot.send_message(chat_id=post["author_id"], text=(
            f"❌ Ваше объявление (ID: {post_id}) отклонено.\n"
            f"Причина: «{reason}»\n\n"
            f"Проверьте требования и попробуйте отправить снова."
        ))
    except Exception:
        logging.debug("Не удалось уведомить автора об отклонении (возможно, заблокировал бота).")

    # уведомление в мод.чат о том кто и почему отклонил
    try:
        await bot.send_message(chat_id=MODER_CHAT_ID, text=(
            f"❌ Отклонено\n"
            f"Причина: «{reason}»\n"
            f"Пост id: {post_id}"
        ))
    except Exception:
        logging.exception("Не удалось отправить сообщение в мод.чат о причине")

    # уведомление владельцам проекта (ADMINS) с полной информацией
    for owner in ADMINS:
        try:
            await bot.send_message(owner, (
                f"🔴 Пост {post_id} отклонён в {now_str()}.\n"
                f"Модератор: {moderator_info}\n"
                f"Причина: {reason}\n"
                f"Автор id: {post['author_id']}, username: {post['author_username']}\n"
                f"Содержимое (caption):\n{post['caption']}"
            ))
        except Exception:
            logging.debug("Не удалось уведомить владельца %s", owner)

    # пометим / отредактируем сообщение с кнопками (чтобы видно было, что обработан)
    try:
        await bot.edit_message_text(chat_id=MODER_CHAT_ID, message_id=post["mod_message_id"],
                                    text=f"❌ Отклонено {moderator_info} — причина: {reason}\nПост id:{post_id}")
    except Exception:
        # игнорируем ошибки редактирования
        pass

    # очистка
    pending_posts.pop(post_id, None)
    awaiting_reasons.pop(reply_to.message_id, None)

# ---------------- Fallback (прочие текстовые сообщения) ----------------
@router.message(F.text)
async def fallback_text(msg: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await msg.answer("Я ожидаю от тебя действие: нажми кнопку 📦 Разместить объявление или ℹ️ Инфо.", reply_markup=kb_main())
        return
    # Напоминаем в зависимости от состояния
    if current_state == PostForm.photos:
        await msg.answer("Отправьте фото или напишите 'готово'.")
    elif current_state == PostForm.price:
        await msg.answer("Укажите цену (например: 2 990 или 'договорная').")
    elif current_state == PostForm.condition:
        await msg.answer("Укажите состояние (например: новое или б/у).")
    elif current_state == PostForm.description:
        await msg.answer("Введите описание товара (коротко, max 700 символов).")
    elif current_state == PostForm.contact:
        await msg.answer("Введите контакт — обязательно @username (например: @ivan).")
    elif current_state == PostForm.city:
        await msg.answer("Укажите город (необязательно) или 'нет'.")
    else:
        await msg.answer("Неизвестное состояние. Нажмите ❌ Отмена для выхода.", reply_markup=kb_main())

# ---------------- graceful shutdown ----------------
async def on_shutdown():
    try:
        if lock_file.exists():
            lock_file.unlink(missing_ok=True)
    except Exception:
        pass
    await bot.session.close()

# ---------------- run ----------------
async def main():
    logging.info("Bot starting")
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()

if __name__ == "__main__":
    asyncio.run(main())