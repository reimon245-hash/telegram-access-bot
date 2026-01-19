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

# Новые переменные для вебхуков
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Например: https://mybot.onrender.com
PORT = int(os.environ.get("PORT", 8000))  # Render сам задаёт PORT

if not TELEGRAM_TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)
if not GOOGLE_CREDENTIALS_JSON:
    print("❌ ОШИБКА: GOOGLE_CREDENTIALS_JSON не установлен!")
    sys.exit(1)
if not WEBHOOK_URL:
    print("❌ ОШИБКА: WEBHOOK_URL не установлен!")
    sys.exit(1)

# === Логирование ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === Google Sheets клиент (singleton) ===
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

def refresh_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]])

# === Логика получения данных ===
async def fetch_user_data(user_id: str) -> str:
    try:
        sheet = GoogleSheetsClient().get_worksheet()
        records = sheet.get_all_records()

        user_record = next((r for r in records if str(r.get("ДОСТУП", "")).strip() == user_id), None)
        if not user_record:
            return "❌ У вас нет доступа к системе."

        info_field = str(user_record.get("ИНФОРМАЦИЯ", "")).strip()
        if not info_field:
            return "📭 Нет доступных данных."

        target_ids = parse_id_ranges(info_field)
        if not target_ids:
            return "⚠️ Не удалось распознать ID объектов."

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

        messages = []
        found = 0
        for obj_id in target_ids:
            if obj_id in obj_map:
                found += 1
                obj = obj_map[obj_id]
                messages.append(f"📍 <b>Адрес:</b> {obj['address']}\n🔐 <b>Код:</b> <code>{obj['code']}</code>")

        if messages:
            return f"✅ Доступно кодов: {found}/{len(target_ids)}\n\n" + "\n".join(messages)
        else:
            return "📭 Не найдено ни одного объекта по вашим ID."

    except Exception as e:
        logger.error(f"Ошибка при получении данных: {e}")
        return "❌ Ошибка сервера. Попробуйте позже."

# === Обработчики команд ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
    await update.message.reply_text("Загружаю данные...", parse_mode="HTML")
    result = await fetch_user_data(str(user.id))
    await update.message.reply_text(result, reply_markup=refresh_button(), parse_mode="HTML")

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} обновляет данные")
    await query.edit_message_text("🔄 Обновляю...", parse_mode="HTML")
    result = await fetch_user_data(str(user.id))
    await query.edit_message_text(result, reply_markup=refresh_button(), parse_mode="HTML")

# === Обработка ошибок ===
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Произошла ошибка. Администратор уведомлён.")
        except Exception:
            pass

# === Запуск через вебхук ===
def main():
    logger.info("🚀 Запуск Telegram бота в режиме вебхука...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(refresh_callback, pattern="^refresh$"))
    app.add_error_handler(error_handler)

    # 🔥 Запуск вебхука
    webhook_path = f"/{TELEGRAM_TOKEN}"
    full_webhook_url = WEBHOOK_URL + webhook_path

    logger.info(f"📡 Устанавливаю вебхук: {full_webhook_url}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path.lstrip("/"),
        webhook_url=full_webhook_url
    )

if __name__ == "__main__":
    main()

