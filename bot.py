def main():
    # Отладка версий
    import telegram
    import telegram.ext
    print(f"PTB version: {telegram.__version__}")
    print(f"PTB ext version: {telegram.ext.__version__}")
    # ... остальной код

import os
import logging
import json
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
GOOGLE_SHEET_NAME = "teleg-bot-passw"
WORKSHEET_NAME = "page1"

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Подключение к Google Sheets ===
def get_sheet():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", 
                 "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)
        return sheet
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        raise

# === Логика обработки диапазонов ===
def parse_id_ranges(range_str: str):
    ids = set()
    if not range_str or not range_str.strip():
        return []
    
    parts = range_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start, end = map(int, part.split("-"))
                if start <= end:
                    ids.update(range(start, end + 1))
            except ValueError as e:
                logger.warning(f"Ошибка парсинга диапазона {part}: {e}")
                continue
        else:
            try:
                ids.add(int(part))
            except ValueError as e:
                logger.warning(f"Ошибка парсинга ID {part}: {e}")
                continue
    return sorted(ids)

def get_refresh_button():
    keyboard = [[InlineKeyboardButton("🔄 ОБНОВИТЬ КОДЫ", callback_data="refresh")]]
    return InlineKeyboardMarkup(keyboard)

async def fetch_user_data(user_id: str):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        logger.info(f"Загружено {len(records)} записей для пользователя {user_id}")

        user_row = None
        for row in records:
            access_value = str(row.get("ДОСТУП", "")).strip()
            if access_value == user_id:
                user_row = row
                break

        if not user_row:
            return "❌ У вас нет доступа к данным."

        info_str = str(user_row.get("ИНФОРМАЦИЯ", "")).strip()
        if not info_str:
            return "ℹ️ Нет данных для отображения."

        target_ids = parse_id_ranges(info_str)
        if not target_ids:
            return "⚠️ Не удалось распознать ID или диапазоны."

        all_objects = {}
        for row in records:
            try:
                obj_id = int(row["ID"])
                all_objects[obj_id] = {
                    "Адрес": row.get("Адрес", ""),
                    "Код": row.get("Код", "")
                }
            except (ValueError, KeyError) as e:
                logger.debug(f"Пропуск строки: {e}")
                continue

        messages = []
        for tid in target_ids:
            if tid in all_objects:
                obj = all_objects[tid]
                messages.append(f"📍 *Адрес:* {obj['Адрес']}\n🔑 *Код:* `{obj['Код']}`")
            else:
                messages.append(f"❌ Объект с ID {tid} не найден.")

        if messages:
            return "\n\n".join(messages)
        else:
            return "📭 Нет доступных объектов."

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON: {e}")
        return "❌ Ошибка конфигурации бота."
    except Exception as e:
        logger.exception("Ошибка при загрузке данных")
        return f"❌ Ошибка сервера: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"Пользователь {user.id} ({user.username}) запустил бота")
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\nЗагружаю данные...",
        parse_mode='Markdown'
    )
    
    user_id = str(user.id)
    data_text = await fetch_user_data(user_id)
    await update.message.reply_text(
        data_text, 
        reply_markup=get_refresh_button(),
        parse_mode='Markdown'
    )

async def refresh_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия кнопки обновления"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    logger.info(f"Пользователь {user.id} обновил данные")
    
    await query.edit_message_text(
        text="🔄 Обновляю данные...",
        parse_mode='Markdown'
    )
    
    user_id = str(user.id)
    new_data = await fetch_user_data(user_id)
    await query.edit_message_text(
        text=new_data, 
        reply_markup=get_refresh_button(),
        parse_mode='Markdown'
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ Произошла ошибка. Попробуйте позже."
        )

def main():
    """Основная функция запуска бота"""
    # Проверка переменных окружения
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен")
        return
    if not CREDENTIALS_JSON:
        logger.error("GOOGLE_CREDENTIALS_JSON не установлен")
        return
    
    try:
        # Создание Application
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Добавление обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(refresh_button, pattern="^refresh$"))
        
        # Обработчик ошибок
        application.add_error_handler(error_handler)
        
        logger.info("Бот запускается...")
        
        # Запуск бота
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {e}")

if __name__ == "__main__":
    main()

