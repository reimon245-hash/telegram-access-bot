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

# === 3. Singleton-клиент для Google Sheets (инициализируется один раз) ===
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

# Парсинг диапазонов ID (например: "1-5,7,10")
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

# Кнопка обновления данных
def refresh_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]])

# === 5. Логика получения данных из Google Sheets по ID пользователя ===
async def fetch_user_data(user_id: str) -> dict:
    """
    Возвращает словарь:
    - 'has_access': bool
    - 'message': str
    """
    try:
        sheet = GoogleSheetsClient().get_worksheet()
        records = sheet.get_all_records(
            expected_headers=["ID", "Адрес", "Код", "ДОСТУП", "Сотрудники по ID", "ИНФОРМАЦИЯ"]
        )

        # Поиск записи по полю "ДОСТУП"
        user_record = next((r for r in records if str(r.get("ДОСТУП", "")).strip() == user_id), None)
        if not user_record:
            return {
                "has_access": False,
                "message": f"Ваш телеграм ID — <code>{user_id}</code>. Передайте его Роману."
            }

        info_field = str(user_record.get("ИНФОРМАЦИЯ", "")).strip()
        if not info_field:
            return {
                "has_access": True,
                "message": "📭 Нет доступных данных."
            }

        target_ids = parse_id_ranges(info_field)
        if not target_ids:
            return {
                "has_access": True,
                "message": "⚠️ Не удалось распознать ID объектов."
            }

        # Составление карты объектов по ID
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

        # Формирование сообщения
        messages = []
        found = 0
        for obj_id in target_ids:
            if obj_id in obj_map:
                found += 1
                obj = obj_map[obj_id]
                messages.append(f"{obj['address']}\n<b>Код</b> <code>{obj['code']}</code>")

        if messages:
            message = f"✅ Доступно кодов: {found}/{len(target_ids)}\n\n" + "\n\n".join(messages)
        else:
            message = "📭 Не найдено ни одного объекта по вашим ID."

        return {
            "has_access": True,
            "message": message
        }

    except Exception as e:
        logger.error(f"Ошибка при получении данных: {e}")
        return {
            "has_access": False,
            "message": "❌ Ошибка сервера. Попробуйте позже."
        }

# === 6. Обработчики команд Telegram ===

# Команда /start — загрузка данных пользователя
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
    await update.message.reply_text("Загружаю данные...", parse_mode="HTML")
    result = await fetch_user_data(str(user.id))
    await update.message.reply_text(
        result["message"],
        reply_markup=refresh_button(),
        parse_mode="HTML"
    )

# Обработка нажатия кнопки "Обновить"
async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} обновляет данные")
    await query.edit_message_text("🔄 Обновляю...", parse_mode="HTML")
    result = await fetch_user_data(str(user.id))
    await query.edit_message_text(
        result["message"],
        reply_markup=refresh_button(),
        parse_mode="HTML"
    )

# === 7. Обработка ошибок Telegram API ===
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Произошла ошибка. Администратор уведомлён.")
        except Exception:
            pass

# === 8. Запуск бота через long polling (без вебхуков) ===
def main():
    logger.info("🚀 Запуск Telegram бота в режиме long polling...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(refresh_callback, pattern="^refresh$"))
    app.add_error_handler(error_handler)

    # Запуск бота без вебхуков — просто long polling
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    logger.info("🛑 Бот остановлен.")

# Точка входа
if __name__ == "__main__":
    main()
