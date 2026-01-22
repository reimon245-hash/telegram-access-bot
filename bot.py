import os
import sys
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# === Вспомогательные функции ===

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
        for j in range(COLS):
            idx = i + j
            if idx >= len(all_ids):
                break
            obj_id = all_ids[idx]
            data = obj_map[obj_id]

            # Формат кнопки: всегда компактный
            if obj_id == expanded_obj_id:
                # Показываем код прямо в кнопке (без жирного, но чётко)
                button_text = f"{data['address']}\nКод: {data['code']}"
            else:
                button_text = data["address"]

            row.append(InlineKeyboardButton(button_text, callback_data=f"show_{obj_id}"))

        buttons.append(row)
        i += COLS

    buttons.append([InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")])
    return InlineKeyboardMarkup(buttons)

def build_no_access_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]])

# === Получение данных ===
async def fetch_user_objects(user_id: str):
    try:
        sheet = GoogleSheetsClient().get_worksheet()
        records = sheet.get_all_records(
            expected_headers=["ID", "Адрес", "Код", "ДОСТУП", "Сотрудники по ID", "ИНФОРМАЦИЯ"]
        )

        user_record = None
        for r in records:
            access_field = str(r.get("ДОСТУП", "")).strip()
            if access_field == user_id:
                user_record = r
                break

        if not user_record:
            return None

        info_field = str(user_record.get("ИНФОРМАЦИЯ", "")).strip()
        target_ids = parse_id_ranges(info_field)
        if not target_ids:
            return {}

        obj_map = {}
        for r in records:
            try:
                raw_id = r.get("ID")
                if raw_id is None or raw_id == "":
                    continue
                obj_id = int(raw_id)
                if obj_id in target_ids:
                    address = r.get("Адрес") or "Не указан"
                    code = r.get("Код") or "Не указан"
                    obj_map[obj_id] = {"address": str(address), "code": str(code)}
            except (ValueError, TypeError, AttributeError):
                continue

        return obj_map

    except Exception as e:
        logger.error(f"Ошибка при получении данных из Google Sheets: {e}", exc_info=True)
        return None

# === Обработчики ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
    msg = await update.message.reply_text("Загружаю данные...", reply_markup=build_no_access_keyboard())

    obj_map = await fetch_user_objects(str(user.id))
    if obj_map is None:
        text = f"Ваш телеграм ID — <code>{user.id}</code>. Передайте его Роману."
        await msg.edit_text(text, reply_markup=build_no_access_keyboard(), parse_mode="HTML")
        return

    if not obj_map:
        await msg.edit_text("📭 Нет доступных объектов.", reply_markup=build_no_access_keyboard())
        return

    context.chat_data["obj_map"] = obj_map
    context.chat_data["expanded"] = None
    keyboard = build_keyboard(obj_map)
    await msg.edit_text("Выберите объект:", reply_markup=keyboard)

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} нажал «ОБНОВИТЬ»")

    obj_map = await fetch_user_objects(str(user.id))

    if obj_map is None:
        text = f"Ваш телеграм ID — <code>{user.id}</code>. Передайте его Роману."
        await query.edit_message_text(text, reply_markup=build_no_access_keyboard(), parse_mode="HTML")
        context.chat_data.pop("obj_map", None)
        context.chat_data.pop("expanded", None)
        return

    if not obj_map:
        await query.edit_message_text("📭 Нет доступных объектов.", reply_markup=build_no_access_keyboard())
        context.chat_data.pop("obj_map", None)
        context.chat_data.pop("expanded", None)
        return

    context.chat_data["obj_map"] = obj_map
    context.chat_data["expanded"] = None
    keyboard = build_keyboard(obj_map)
    await query.edit_message_text("Выберите объект:", reply_markup=keyboard)

async def show_code_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    obj_map = context.chat_data.get("obj_map")
    if not obj_map:
        user = query.from_user
        obj_map = await fetch_user_objects(str(user.id))
        if obj_map is None:
            text = f"Ваш телеграм ID — <code>{user.id}</code>. Передайте его Роману."
            await query.edit_message_text(text, reply_markup=build_no_access_keyboard(), parse_mode="HTML")
            context.chat_data.clear()
            return
        if not obj_map:
            await query.edit_message_text("📭 Нет доступных объектов.", reply_markup=build_no_access_keyboard())
            context.chat_data.clear()
            return
        context.chat_data["obj_map"] = obj_map
        context.chat_data["expanded"] = None

    try:
        obj_id = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Некорректный запрос.")
        return

    if obj_id not in obj_map:
        await query.edit_message_text("❌ Объект не найден.")
        return

    current_expanded = context.chat_data.get("expanded")
    if current_expanded == obj_id:
        new_expanded = None
    else:
        new_expanded = obj_id

    if current_expanded == new_expanded:
        return

    context.chat_data["expanded"] = new_expanded
    keyboard = build_keyboard(obj_map, expanded_obj_id=new_expanded)
    await query.edit_message_reply_markup(reply_markup=keyboard)

# === Обработка ошибок ===
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error(f"Произошла ошибка: {error}", exc_info=True)

    if "Message is not modified" in str(error):
        return

    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Произошла ошибка. Администратор уведомлён.")
        except Exception:
            pass

# === Запуск ===
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
