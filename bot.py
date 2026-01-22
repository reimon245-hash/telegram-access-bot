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

# === 1. Конфигурация: получение токенов и настроек из переменных окружения ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
GOOGLE_SHEET_NAME = "teleg-bot-passw"
WORKSHEET_NAME = "page1"

# Проверка обязательных переменных
if not TELEGRAM_TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)
if not GOOGLE_CREDENTIALS_JSON:
    print("❌ ОШИБКА: GOOGLE_CREDENTIALS_JSON не установлен!")
    sys.exit(1)

# === 2. Настройка логирования ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === 3. Singleton-клиент для Google Sheets ===
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

# === 4. Вспомогательные функции ===

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

def build_message_and_keyboard(obj_map, target_ids, revealed_id=None):
    """
    Формирует текст сообщения и клавиатуру.
    - obj_map: {id: {"address": ..., "code": ...}}
    - target_ids: список ID, доступных пользователю
    - revealed_id: ID, чей код сейчас показан (или None)
    """
    lines = []
    buttons = []

    for obj_id in target_ids:
        obj = obj_map.get(obj_id)
        if not obj:
            continue
        address = obj["address"]
        code = obj["code"]

        if obj_id == revealed_id:
            lines.append(f"{address}\n<b>Код</b> <code>{code}</code>")
            buttons.append(InlineKeyboardButton("Скрыть", callback_data=f"hide_{obj_id}"))
        else:
            lines.append(address)
            buttons.append(InlineKeyboardButton("Показать код", callback_data=f"show_{obj_id}"))

    text = "\n\n".join(lines) if lines else "📭 Нет доступных объектов."
    # Одна строка кнопок — по одной на каждый объект
    keyboard = [buttons] if buttons else []
    return text, InlineKeyboardMarkup(keyboard)

async def fetch_user_data_and_build_ui(user_id: str, revealed_id=None):
    """
    Возвращает словарь:
    - 'has_access': bool
    - 'text': str
    - 'reply_markup': InlineKeyboardMarkup или None
    - 'target_ids': list (для внутреннего использования)
    - 'obj_map': dict
    """
    try:
        sheet = GoogleSheetsClient().get_worksheet()
        records = sheet.get_all_records(
            expected_headers=["ID", "Адрес", "Код", "ДОСТУП", "Сотрудники по ID", "ИНФОРМАЦИЯ"]
        )

        user_record = next((r for r in records if str(r.get("ДОСТУП", "")).strip() == user_id), None)
        if not user_record:
            return {
                "has_access": False,
                "text": f"Ваш телеграм ID — <code>{user_id}</code>. Передайте его Роману.",
                "reply_markup": InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]),
                "target_ids": [],
                "obj_map": {}
            }

        info_field = str(user_record.get("ИНФОРМАЦИЯ", "")).strip()
        target_ids = parse_id_ranges(info_field)
        if not target_ids:
            return {
                "has_access": True,
                "text": "⚠️ Не удалось распознать ID объектов.",
                "reply_markup": InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]),
                "target_ids": [],
                "obj_map": {}
            }

        obj_map = {}
        for r in records:
            try:
                obj_id = int(r.get("ID", 0))
                if obj_id:
                    obj_map[obj_id] = {
                        "address": r.get("Адрес", "Не указан"),
                        "code": r.get("Код", "Не указан")
                    }
            except (ValueError, TypeError):
                continue

        # Удаляем недоступные ID
        valid_target_ids = [oid for oid in target_ids if oid in obj_map]

        if not valid_target_ids:
            return {
                "has_access": True,
                "text": "📭 Не найдено ни одного объекта по вашим ID.",
                "reply_markup": InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]),
                "target_ids": [],
                "obj_map": {}
            }

        text, reply_markup = build_message_and_keyboard(obj_map, valid_target_ids, revealed_id=revealed_id)
        return {
            "has_access": True,
            "text": text,
            "reply_markup": reply_markup,
            "target_ids": valid_target_ids,
            "obj_map": obj_map
        }

    except Exception as e:
        logger.error(f"Ошибка при получении данных: {e}")
        return {
            "has_access": False,
            "text": "❌ Ошибка сервера. Попробуйте позже.",
            "reply_markup": InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]),
            "target_ids": [],
            "obj_map": {}
        }

# === 5. Обработчики ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
    await update.message.reply_text("Загружаю данные...", parse_mode="HTML")
    result = await fetch_user_data_and_build_ui(str(user.id))
    await update.message.reply_text(
        result["text"],
        reply_markup=result["reply_markup"],
        parse_mode="HTML"
    )

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} обновляет данные")
    result = await fetch_user_data_and_build_ui(str(user.id))
    await query.edit_message_text(
        result["text"],
        reply_markup=result["reply_markup"],
        parse_mode="HTML"
    )

async def show_hide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if data.startswith("show_"):
        try:
            obj_id = int(data.split("_", 1)[1])
        except ValueError:
            await query.edit_message_text("❌ Некорректный запрос.")
            return
        # Загружаем данные и открываем указанный код
        result = await fetch_user_data_and_build_ui(str(user.id), revealed_id=obj_id)
        await query.edit_message_text(
            result["text"],
            reply_markup=result["reply_markup"],
            parse_mode="HTML"
        )
    elif data.startswith("hide_"):
        # Просто обновляем без раскрытого кода
        result = await fetch_user_data_and_build_ui(str(user.id))
        await query.edit_message_text(
            result["text"],
            reply_markup=result["reply_markup"],
            parse_mode="HTML"
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Произошла ошибка. Администратор уведомлён.")
        except Exception:
            pass

# === 6. Запуск бота ===
def main():
    logger.info("🚀 Запуск Telegram бота в режиме long polling...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(refresh_callback, pattern="^refresh$"))
    app.add_handler(CallbackQueryHandler(show_hide_callback, pattern="^(show_|hide_)"))
    app.add_error_handler(error_handler)

    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    logger.info("🛑 Бот остановлен.")

if __name__ == "__main__":
    main()
