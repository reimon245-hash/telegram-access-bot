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

# Генерация клавиатуры с кнопками "Показать" или открытым кодом
def build_keyboard_and_text(user_id: str, revealed_obj_id: int = None):
    try:
        sheet = GoogleSheetsClient().get_worksheet()
        records = sheet.get_all_records(
            expected_headers=["ID", "Адрес", "Код", "ДОСТУП", "Сотрудники по ID", "ИНФОРМАЦИЯ"]
        )

        # Найти пользователя
        user_record = next((r for r in records if str(r.get("ДОСТУП", "")).strip() == user_id), None)
        if not user_record:
            return {
                "text": f"Ваш телеграм ID — <code>{user_id}</code>. Передайте его Роману.",
                "keyboard": [[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]
            }

        info_field = str(user_record.get("ИНФОРМАЦИЯ", "")).strip()
        if not info_field:
            return {
                "text": "📭 Нет доступных данных.",
                "keyboard": [[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]
            }

        target_ids = parse_id_ranges(info_field)
        if not target_ids:
            return {
                "text": "⚠️ Не удалось распознать ID объектов.",
                "keyboard": [[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]
            }

        # Составить карту объектов
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

        # Формируем текст и кнопки
        lines = []
        buttons = []

        found_any = False
        for obj_id in target_ids:
            if obj_id not in obj_map:
                continue
            found_any = True
            obj = obj_map[obj_id]
            if obj_id == revealed_obj_id:
                lines.append(f"{obj['address']}\n<b>Код</b>: <code>{obj['code']}</code>")
                buttons.append([InlineKeyboardButton("Скрыть", callback_data=f"hide_{obj_id}")])
            else:
                lines.append(f"{obj['address']}\nКод: 🔒 Скрыт")
                buttons.append([InlineKeyboardButton("Показать", callback_data=f"show_{obj_id}")])

        if not found_any:
            return {
                "text": "📭 Не найдено ни одного объекта по вашим ID.",
                "keyboard": [[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]
            }

        full_text = "\n\n".join(lines)
        # Добавляем кнопку обновления вниз
        buttons.append([InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")])
        return {"text": full_text, "keyboard": buttons}

    except Exception as e:
        logger.error(f"Ошибка при построении интерфейса: {e}")
        return {
            "text": "❌ Ошибка сервера. Попробуйте позже.",
            "keyboard": [[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]
        }

# === 5. Обработчики команд ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
    await update.message.reply_text("Загружаю данные...", parse_mode="HTML")
    ui = build_keyboard_and_text(str(user.id))
    await update.message.reply_text(
        ui["text"],
        reply_markup=InlineKeyboardMarkup(ui["keyboard"]),
        parse_mode="HTML"
    )

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} обновляет данные")
    ui = build_keyboard_and_text(str(user.id))
    await query.edit_message_text(
        ui["text"],
        reply_markup=InlineKeyboardMarkup(ui["keyboard"]),
        parse_mode="HTML"
    )

async def show_hide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    user_id = str(user.id)

    revealed_id = None
    if data.startswith("show_"):
        try:
            revealed_id = int(data.split("_", 1)[1])
        except ValueError:
            pass
    elif data.startswith("hide_"):
        revealed_id = None  # скрываем всё
    # Если "refresh" — уже обрабатывается другим хендлером

    ui = build_keyboard_and_text(user_id, revealed_obj_id=revealed_id)
    await query.edit_message_text(
        ui["text"],
        reply_markup=InlineKeyboardMarkup(ui["keyboard"]),
        parse_mode="HTML"
    )

# === 6. Обработка ошибок ===
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Произошла ошибка. Администратор уведомлён.")
        except Exception:
            pass

# === 7. Запуск бота ===
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
