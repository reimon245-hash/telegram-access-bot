import os
import sys
import json
import logging
import asyncio
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

# === Google Sheets клиент (Singleton) ===
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

def build_keyboard(obj_map, code_shown_obj_id=None):
    buttons = []
    all_ids = list(obj_map.keys())
    MAX_HALF_WIDTH_CHARS = 17  # ← изменено с 20 на 17

    i = 0
    while i < len(all_ids):
        obj_id = all_ids[i]
        data = obj_map[obj_id]

        if obj_id == code_shown_obj_id:
            button_text = f"🔑 Код: {data['code']} 🔑"
        else:
            button_text = data["address"]

        # Если текст длиннее 17 символов — кнопка на всю ширину
        if len(button_text) > MAX_HALF_WIDTH_CHARS:
            buttons.append([InlineKeyboardButton(button_text, callback_data=f"show_{obj_id}")])
            i += 1
        else:
            # Пытаемся добавить вторую кнопку в строку
            row = [InlineKeyboardButton(button_text, callback_data=f"show_{obj_id}")]
            if i + 1 < len(all_ids):
                next_obj_id = all_ids[i + 1]
                next_data = obj_map[next_obj_id]
                if next_obj_id == code_shown_obj_id:
                    next_text = f"🔑 Код: {next_data['code']} 🔑"
                else:
                    next_text = next_data["address"]

                if len(next_text) <= MAX_HALF_WIDTH_CHARS:
                    row.append(InlineKeyboardButton(next_text, callback_data=f"show_{next_obj_id}"))
                    i += 2
                else:
                    i += 1
            else:
                i += 1
            buttons.append(row)

    buttons.append([InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")])
    return InlineKeyboardMarkup(buttons)

def build_no_access_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]])

async def show_no_access_message(query_or_msg, user_id):
    text = f"Ваш телеграм ID — <code>{user_id}</code>. Передайте его Роману."
    if hasattr(query_or_msg, 'edit_message_text'):
        await query_or_msg.edit_message_text(text, reply_markup=build_no_access_keyboard(), parse_mode="HTML")
    else:
        await query_or_msg.reply_text(text, reply_markup=build_no_access_keyboard(), parse_mode="HTML")

# === Получение данных из Google Sheets ===
async def fetch_user_objects(user_id: str):
    try:
        sheet = GoogleSheetsClient().get_worksheet()
        records = sheet.get_all_records(
            expected_headers=["ID", "Адрес", "Старый код", "Код", "ДОСТУП", "Сотрудники по ID", "ИНФОРМАЦИЯ", "Детали"]
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
                    details = str(r.get("Детали", "")).strip() or "Детали отсутствуют"
                    obj_map[obj_id] = {
                        "address": str(address),
                        "code": str(code),
                        "details": details
                    }
            except (ValueError, TypeError, AttributeError):
                continue

        return obj_map

    except Exception as e:
        logger.error(f"Ошибка при получении данных из Google Sheets: {e}", exc_info=True)
        return None

# === Фоновая задача: скрыть код через 7 минут ===
async def auto_hide_code(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, obj_id: int):
    await asyncio.sleep(420)  # 7 минут = 420 секунд
    try:
        if context.chat_data.get("code_shown") == obj_id:
            context.chat_data["code_shown"] = None
            obj_map = context.chat_data.get("obj_map")
            if obj_map:
                keyboard = build_keyboard(obj_map)
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="Выберите объект:",
                    reply_markup=keyboard
                )
    except Exception as e:
        logger.debug(f"Авто-скрытие кода: {e}")

# === Обработчики ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
    msg = await update.message.reply_text("Загружаю данные...", reply_markup=build_no_access_keyboard())

    obj_map = await fetch_user_objects(str(user.id))
    if obj_map is None:
        await show_no_access_message(msg, user.id)
        return

    if not obj_map:
        await msg.edit_text("📭 Нет доступных объектов.", reply_markup=build_no_access_keyboard())
        return

    context.chat_data["obj_map"] = obj_map
    context.chat_data["code_shown"] = None
    old_task = context.chat_data.get("hide_task")
    if old_task and not old_task.done():
        old_task.cancel()
    context.chat_data["hide_task"] = None

    keyboard = build_keyboard(obj_map)
    await msg.edit_text("Выберите объект:", reply_markup=keyboard)

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    logger.info(f"🔄 Пользователь {user.id} нажал «ОБНОВИТЬ»")

    obj_map = await fetch_user_objects(str(user.id))

    if obj_map is None:
        await show_no_access_message(query, user.id)
        context.chat_data.clear()
        return

    if not obj_map:
        await query.edit_message_text("📭 Нет доступных объектов.", reply_markup=build_no_access_keyboard())
        context.chat_data.clear()
        return

    context.chat_data["obj_map"] = obj_map
    context.chat_data["code_shown"] = None
    old_task = context.chat_data.get("hide_task")
    if old_task and not old_task.done():
        old_task.cancel()
    context.chat_data["hide_task"] = None

    keyboard = build_keyboard(obj_map)
    await query.edit_message_text("Выберите объект:", reply_markup=keyboard)

async def show_code_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = str(user.id)

    # 🔒 Проверка доступа при каждом нажатии
    obj_map = await fetch_user_objects(user_id)
    if obj_map is None:
        await show_no_access_message(query, user.id)
        context.chat_data.clear()
        return

    if not obj_map:
        await query.edit_message_text("📭 Нет доступных объектов.", reply_markup=build_no_access_keyboard())
        context.chat_data.clear()
        return

    context.chat_data["obj_map"] = obj_map

    try:
        obj_id = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Некорректный запрос.")
        return

    if obj_id not in obj_map:
        await query.edit_message_text("❌ Объект не найден.")
        return

    current_code_shown = context.chat_data.get("code_shown")
    old_task = context.chat_data.get("hide_task")

    if current_code_shown == obj_id:
        # Скрыть вручную
        if old_task and not old_task.done():
            old_task.cancel()
        context.chat_data["code_shown"] = None
        context.chat_data["hide_task"] = None
        keyboard = build_keyboard(obj_map)
        await query.edit_message_text(
            text="Выберите объект:",
            reply_markup=keyboard
        )
    else:
        # Показать код + детали
        if old_task and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(
            auto_hide_code(context, query.message.chat_id, query.message.message_id, obj_id)
        )
        context.chat_data["hide_task"] = task
        context.chat_data["code_shown"] = obj_id

        details = obj_map[obj_id]["details"]
        keyboard = build_keyboard(obj_map, code_shown_obj_id=obj_id)
        await query.edit_message_text(
            text=f"Выберите объект:\n\n📍 <b>Детали:</b> {details}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

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
