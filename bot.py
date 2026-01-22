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

def refresh_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]])

def build_objects_list_message(objects, revealed_code_id=None):
    """Формирует текст сообщения со списком адресов и, при необходимости, одним открытым кодом."""
    if not objects:
        return "📭 Нет доступных объектов."

    lines = []
    for obj in objects:
        address = obj["address"]
        lines.append(address)
        if revealed_code_id == obj["id"]:
            lines.append(f"🔑 Код: <code>{obj['code']}</code>")
    return "\n".join(lines)

def build_inline_keyboard(objects, show_refresh=True):
    """Создаёт кнопки 'Показать код' для каждого объекта + опционально кнопку обновления."""
    buttons = []
    for obj in objects:
        buttons.append([
            InlineKeyboardButton("Показать код", callback_data=f"show_{obj['id']}")
        ])
    if show_refresh:
        buttons.append([InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")])
    return InlineKeyboardMarkup(buttons)

# === 5. Получение данных пользователя из Google Sheets ===
async def fetch_user_objects(user_id: str):
    """
    Возвращает:
      - {'has_access': False, 'message': ...} — если доступа нет
      - {'has_access': True, 'objects': [...]} — если есть
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
            return {"has_access": True, "objects": []}

        target_ids = parse_id_ranges(info_field)
        if not target_ids:
            return {"has_access": True, "objects": []}

        # Составление карты объектов по ID
        obj_map = {}
        for r in records:
            try:
                obj_id = int(r.get("ID", 0))
                if obj_id:
                    obj_map[obj_id] = {
                        "id": obj_id,
                        "address": str(r.get("Адрес", "Не указан")).strip(),
                        "code": str(r.get("Код", "Не указан")).strip()
                    }
            except (ValueError, TypeError):
                continue

        # Формируем список объектов в порядке target_ids
        objects = []
        for obj_id in target_ids:
            if obj_id in obj_map:
                objects.append(obj_map[obj_id])

        return {"has_access": True, "objects": objects}

    except Exception as e:
        logger.error(f"Ошибка при получении данных: {e}")
        return {
            "has_access": False,
            "message": "❌ Ошибка сервера. Попробуйте позже."
        }

# === 6. Обработчики ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
    await update.message.reply_text("Загружаю данные...", parse_mode="HTML")

    result = await fetch_user_objects(str(user.id))

    if not result["has_access"]:
        await update.message.reply_text(
            result["message"],
            reply_markup=refresh_button(),
            parse_mode="HTML"
        )
        return

    objects = result["objects"]
    context.user_data["objects"] = objects

    if not objects:
        await update.message.reply_text(
            "📭 Нет доступных объектов.",
            reply_markup=refresh_button(),
            parse_mode="HTML"
        )
        return

    message_text = build_objects_list_message(objects)
    keyboard = build_inline_keyboard(objects)
    await update.message.reply_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} обновляет данные")
    await query.edit_message_text("🔄 Обновляю...", parse_mode="HTML")

    result = await fetch_user_objects(str(user.id))

    if not result["has_access"]:
        await query.edit_message_text(
            result["message"],
            reply_markup=refresh_button(),
            parse_mode="HTML"
        )
        return

    objects = result["objects"]
    context.user_data["objects"] = objects

    if not objects:
        await query.edit_message_text(
            "📭 Нет доступных объектов.",
            reply_markup=refresh_button(),
            parse_mode="HTML"
        )
        return

    message_text = build_objects_list_message(objects)
    keyboard = build_inline_keyboard(objects)
    await query.edit_message_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def show_code_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Извлекаем ID объекта из callback_data (формат: "show_123")
    try:
        obj_id = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Некорректный запрос.")
        return

    objects = context.user_data.get("objects", [])
    if not objects:
        await query.edit_message_text("📭 Данные устарели. Нажмите «ОБНОВИТЬ».", parse_mode="HTML")
        return

    # Проверяем, существует ли объект с таким ID
    target_obj = next((obj for obj in objects if obj["id"] == obj_id), None)
    if not target_obj:
        await query.edit_message_text("❌ Объект не найден.", parse_mode="HTML")
        return

    # Формируем сообщение с открытым кодом только для этого объекта
    message_text = build_objects_list_message(objects, revealed_code_id=obj_id)
    keyboard = build_inline_keyboard(objects)  # те же кнопки, можно снова выбрать другой
    await query.edit_message_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

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
    app.add_handler(CallbackQueryHandler(show_code_callback, pattern=r"^show_\d+$"))
    app.add_error_handler(error_handler)

    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    logger.info("🛑 Бот остановлен.")

if __name__ == "__main__":
    main()
