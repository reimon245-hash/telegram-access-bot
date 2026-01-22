import os
import sys
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from google.oauth2.service_account import Credentials
import gspread

# === Конфигурация ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
GOOGLE_SHEET_NAME = "teleg-bot-passw"
WORKSHEET_NAME = "page1"

if not TELEGRAM_TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)
if not GOOGLE_CREDENTIALS_JSON:
    print("❌ ОШИБКА: GOOGLE_CREDENTIALS_JSON не установлен!")
    sys.exit(1)

# === Логирование ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === Google Sheets клиент ===
class GoogleSheetsClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_client()
        return cls._instance

    def _init_client(self):
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive"
        ]
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        self.client = gspread.authorize(credentials)
        logger.info("✅ Google Sheets клиент готов")

    def get_worksheet(self):
        sheet = self.client.open(GOOGLE_SHEET_NAME)
        return sheet.worksheet(WORKSHEET_NAME)

def parse_id_ranges(range_str: str):
    if not range_str or not isinstance(range_str, str):
        return []
    ids = set()
    for part in range_str.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start, end = map(int, part.split("-", 1))
                if start <= end:
                    ids.update(range(start, end + 1))
            else:
                ids.add(int(part))
        except ValueError:
            continue
    return sorted(ids)

def build_keyboard(obj_map, expanded_obj_id=None):
    buttons = []
    all_ids = list(obj_map.keys())
    COLS = 2

    i = 0
    while i < len(all_ids):
        row = []
        added_expanded = False
        for j in range(COLS):
            idx = i + j
            if idx >= len(all_ids):
                break
            obj_id = all_ids[idx]
            data = obj_map[obj_id]

            if obj_id == expanded_obj_id:
                row = [InlineKeyboardButton(f"{data['address']}\nКод: {data['code']}", callback_data=f"show_{obj_id}")]
                buttons.append(row)
                added_expanded = True
                break
            else:
                row.append(InlineKeyboardButton(data["address"], callback_data=f"show_{obj_id}"))

        if added_expanded:
            i += COLS
        else:
            if row:
                buttons.append(row)
            i += COLS

    buttons.append([InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")])
    return InlineKeyboardMarkup(buttons)

def build_no_access_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]])

async def fetch_user_objects(user_id: str):
    try:
        sheet = GoogleSheetsClient().get_worksheet()
        records = sheet.get_all_records(
            expected_headers=["ID", "Адрес", "Код", "ДОСТУП", "Сотрудники по ID", "ИНФОРМАЦИЯ"]
        )

        user_record = next((r for r in records if str(r.get("ДОСТУП", "")).strip() == user_id), None)
        if not user_record:
            return None

        info_field = str(user_record.get("ИНФОРМАЦИЯ", "")).strip()
        target_ids = parse_id_ranges(info_field)
        if not target_ids:
            return {}

        obj_map = {}
        for r in records:
            try:
                obj_id = int(r.get("ID", 0))
                if obj_id in target_ids:
                    obj_map[obj_id] = {
                        "address": r.get("Адрес", "Не указан"),
                        "code": r.get("Код", "Не указан")
                    }
            except (ValueError, TypeError):
                continue
        return obj_map

    except Exception as e:
        logger.error(f"Ошибка при получении данных: {e}")
        return None

async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Безопасное удаление сообщения (игнорирует ошибки)"""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest as e:
        if "not found" not in str(e).lower() and "message to delete not found" not in str(e).lower():
            logger.warning(f"Не удалось удалить сообщение {message_id}: {e}")

async def send_main_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup):
    """Отправляет новое основное сообщение и удаляет старое"""
    chat_id = update.effective_chat.id

    # Удаляем предыдущее основное сообщение, если есть
    old_msg_id = context.chat_data.get("main_message_id")
    if old_msg_id:
        await safe_delete_message(context, chat_id, old_msg_id)

    # Отправляем новое
    if update.callback_query:
        msg = await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        msg = await update.message.reply_text(text, reply_markup=reply_markup)

    # Сохраняем ID
    context.chat_data["main_message_id"] = msg.message_id
    return msg

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")

    obj_map = await fetch_user_objects(str(user.id))
    if obj_map is None:
        text = f"Ваш телеграм ID — <code>{user.id}</code>. Передайте его Роману."
        await send_main_message(update, context, text, build_no_access_keyboard())
        return

    if not obj_map:
        await send_main_message(update, context, "📭 Нет доступных объектов.", build_no_access_keyboard())
        return

    context.chat_data["obj_map"] = obj_map
    context.chat_data["expanded"] = None
    keyboard = build_keyboard(obj_map)
    await send_main_message(update, context, "Выберите объект:", keyboard)

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} обновляет данные")

    obj_map = await fetch_user_objects(str(user.id))
    if obj_map is None:
        text = f"Ваш телеграм ID — <code>{user.id}</code>. Передайте его Роману."
        await send_main_message(update, context, text, build_no_access_keyboard())
        return

    if not obj_map:
        await send_main_message(update, context, "📭 Нет доступных объектов.", build_no_access_keyboard())
        return

    context.chat_data["obj_map"] = obj_map
    context.chat_data["expanded"] = None
    keyboard = build_keyboard(obj_map)
    await send_main_message(update, context, "Выберите объект:", keyboard)

async def show_code_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    obj_map = context.chat_data.get("obj_map")
    if not obj_map:
        await send_main_message(update, context, "⚠️ Данные устарели. Нажмите «ОБНОВИТЬ».", build_no_access_keyboard())
        return

    try:
        obj_id = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await send_main_message(update, context, "❌ Некорректный запрос.", build_no_access_keyboard())
        return

    if obj_id not in obj_map:
        await send_main_message(update, context, "❌ Объект не найден.", build_no_access_keyboard())
        return

    current_expanded = context.chat_data.get("expanded")
    if current_expanded == obj_id:
        context.chat_data["expanded"] = None
    else:
        context.chat_data["expanded"] = obj_id

    keyboard = build_keyboard(obj_map, expanded_obj_id=context.chat_data["expanded"])
    # Обновляем ТОЛЬКО клавиатуру основного сообщения
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest as e:
        # Если сообщение уже удалено — отправим новое
        logger.warning(f"Не удалось обновить клавиатуру: {e}")
        await send_main_message(update, context, "Выберите объект:", keyboard)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Произошла ошибка. Администратор уведомлён.")
        except Exception:
            pass

def main():
    logger.info("🚀 Запуск Telegram бота в режиме long polling...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(refresh_callback, pattern="^refresh$"))
    app.add_handler(CallbackQueryHandler(show_code_callback, pattern=r"^show_\d+$"))
    app.add_error_handler(error_handler)

    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    logger.info("🛑 Бот остановлен.")

if __name__ == "__main__":
    main()
