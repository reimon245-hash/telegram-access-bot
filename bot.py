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
    MessageHandler,
    filters
)
from telegram.error import TelegramError
import gspread
from google.oauth2.service_account import Credentials

# ==================== НАСТРОЙКИ ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    print("ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)

CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
if not CREDENTIALS_JSON:
    print("ОШИБКА: GOOGLE_CREDENTIALS_JSON не установлен!")
    sys.exit(1)

GOOGLE_SHEET_NAME = "teleg-bot-passw"
WORKSHEET_NAME = "page1"

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# ==================== GOOGLE SHEETS ====================
class GoogleSheetsClient:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        try:
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/drive"
            ]
            
            # Парсим JSON из переменной окружения
            creds_dict = json.loads(CREDENTIALS_JSON)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            self.client = gspread.authorize(creds)
            logger.info("✅ Google Sheets клиент инициализирован")
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации Google Sheets: {e}")
            raise
    
    def get_sheet(self):
        """Получить рабочий лист"""
        try:
            spreadsheet = self.client.open(GOOGLE_SHEET_NAME)
            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
            return worksheet
        except Exception as e:
            logger.error(f"❌ Ошибка получения листа: {e}")
            raise

# ==================== УТИЛИТЫ ====================
def parse_id_ranges(range_str: str):
    """Парсинг диапазонов ID из строки"""
    if not range_str or not isinstance(range_str, str):
        return []
    
    ids = set()
    range_str = range_str.strip()
    if not range_str:
        return []
    
    parts = range_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        try:
            if "-" in part:
                start_end = part.split("-")
                if len(start_end) == 2:
                    start = int(start_end[0].strip())
                    end = int(start_end[1].strip())
                    if start <= end:
                        ids.update(range(start, end + 1))
            else:
                ids.add(int(part))
        except ValueError:
            logger.warning(f"Неверный формат диапазона: {part}")
            continue
    
    return sorted(ids)

def create_refresh_button():
    """Создать кнопку обновления"""
    keyboard = [[InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh")]]
    return InlineKeyboardMarkup(keyboard)

# ==================== ОБРАБОТЧИКИ ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        user = update.effective_user
        logger.info(f"🚀 Пользователь {user.id} (@{user.username}) запустил бота")
        
        await update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n"
            "Загружаю данные для вас...",
            parse_mode='HTML'
        )
        
        # Получаем данные
        result = await get_user_data(str(user.id))
        
        await update.message.reply_text(
            result,
            reply_markup=create_refresh_button(),
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Ошибка в start_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при загрузке данных. Попробуйте позже."
        )

async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия кнопки обновления"""
    query = update.callback_query
    await query.answer()
    
    try:
        user = query.from_user
        logger.info(f"🔄 Пользователь {user.id} обновляет данные")
        
        # Обновляем сообщение
        await query.edit_message_text(
            "🔄 Обновляю данные...",
            parse_mode='HTML'
        )
        
        # Получаем новые данные
        result = await get_user_data(str(user.id))
        
        await query.edit_message_text(
            result,
            reply_markup=create_refresh_button(),
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Ошибка в refresh_callback: {e}")
        await query.edit_message_text(
            "❌ Ошибка при обновлении данных.",
            reply_markup=create_refresh_button()
        )

async def get_user_data(user_id: str):
    """Получить данные пользователя из Google Sheets"""
    try:
        # Инициализируем клиент Google Sheets
        gs_client = GoogleSheetsClient()
        sheet = gs_client.get_sheet()
        
        # Получаем все записи
        records = sheet.get_all_records()
        logger.info(f"📊 Загружено {len(records)} записей")
        
        # Ищем пользователя
        user_record = None
        for record in records:
            access_field = str(record.get("ДОСТУП", "")).strip()
            if access_field == user_id:
                user_record = record
                break
        
        if not user_record:
            return "❌ У вас нет доступа к системе."
        
        # Получаем информацию о диапазонах
        info_str = str(user_record.get("ИНФОРМАЦИЯ", "")).strip()
        if not info_str:
            return "📭 Нет доступных данных для отображения."
        
        # Парсим диапазоны
        target_ids = parse_id_ranges(info_str)
        if not target_ids:
            return "⚠️ Не удалось распознать ID объектов."
        
        logger.info(f"🎯 Найдено {len(target_ids)} ID для пользователя {user_id}")
        
        # Создаем словарь всех объектов
        objects_dict = {}
        for record in records:
            try:
                obj_id = int(record.get("ID", 0))
                if obj_id:
                    objects_dict[obj_id] = {
                        "address": record.get("Адрес", "Не указан"),
                        "code": record.get("Код", "Не указан")
                    }
            except (ValueError, TypeError):
                continue
        
        # Формируем сообщение
        messages = []
        found_count = 0
        
        for obj_id in target_ids:
            if obj_id in objects_dict:
                found_count += 1
                obj = objects_dict[obj_id]
                messages.append(
                    f"📍 <b>Адрес:</b> {obj['address']}\n"
                    f"🔐 <b>Код:</b> <code>{obj['code']}</code>\n"
                )
        
        if messages:
            header = f"✅ Найдено объектов: {found_count}/{len(target_ids)}\n\n"
            return header + "\n".join(messages)
        else:
            return "📭 Не найдено ни одного объекта по вашим ID."
            
    except json.JSONDecodeError as e:
        logger.error(f"JSON ошибка: {e}")
        return "❌ Ошибка конфигурации сервиса."
    except Exception as e:
        logger.error(f"Ошибка получения данных: {e}")
        return f"❌ Ошибка сервера: {str(e)}"

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла ошибка. Администратор уведомлен."
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")

# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================
def main():
    """Точка входа в приложение"""
    logger.info("=" * 50)
    logger.info("🚀 Запуск Telegram бота...")
    logger.info("=" * 50)
    
    # Проверяем наличие токена
    if not TELEGRAM_TOKEN:
        logger.critical("❌ TELEGRAM_BOT_TOKEN не найден!")
        sys.exit(1)
    
    try:
        # Создаем Application с явными параметрами
        application = Application.builder() \
            .token(TELEGRAM_TOKEN) \
            .concurrent_updates(True) \
            .build()
        
        # Регистрируем обработчики
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CallbackQueryHandler(refresh_callback, pattern="^refresh$"))
        
        # Глобальный обработчик ошибок
        application.add_error_handler(error_handler)
        
        logger.info("✅ Бот инициализирован")
        logger.info("⏳ Запускаю поллинг...")
        
        # Запускаем бота
        application.run_polling(
            poll_interval=1.0,
            timeout=30,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
