import os
import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

# === Настройки (берутся из переменных окружения) ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# 🔴 ИЗМЕНЕНО: новое имя таблицы и листа
GOOGLE_SHEET_NAME = "teleg-bot-admin"
WORKSHEET_NAME = "info"

# === Подключение к Google Sheets ===
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    sheet = client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)
    return sheet

# === Парсинг диапазонов (1-5,7,10-12) ===
def parse_id_ranges(range_str: str):
    ids = set()
    if not range_str.strip():
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
            except ValueError:
                continue
        else:
            try:
                ids.add(int(part))
            except ValueError:
                continue
    return sorted(ids)

def get_refresh_button():
    keyboard = [[InlineKeyboardButton("ОБНОВИТЬ КОДЫ", callback_data="refresh")]]
    return InlineKeyboardMarkup(keyboard)

async def fetch_user_data(user_id: str):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()

        # 🔴 ИЩЕМ СТРОКУ ПО СТОЛБЦУ "ДОСТУП"
        user_row = None
        for row in records:
            # Важно: ключи в row — это заголовки таблицы!
            if str(row.get("ДОСТУП", "")).strip() == user_id:
                user_row = row
                break

        if not user_row:
            return "У вас нет доступа."

        info_str = str(user_row.get("ИНФОРМАЦИЯ", "")).strip()
        if not info_str:
            return "Нет данных для отображения."

        target_ids = parse_id_ranges(info_str)
        if not target_ids:
            return "Не удалось распознать ID или диапазоны."

        # 🔴 СОБИРАЕМ ВСЕ ОБЪЕКТЫ ПО СТОЛБЦУ "ID объекта"
        all_objects = {}
        for row in records:
            try:
                obj_id = int(row["ID объекта"])  # ← именно так называется столбец!
                all_objects[obj_id] = {
                    "Адрес": row.get("Адрес короткий", ""),
                    "Код": row.get("Код от сейфа", "")
                }
            except (ValueError, KeyError):
                continue

        messages = []
        for tid in target_ids:
            if tid in all_objects:
                obj = all_objects[tid]
                messages.append(f"📍 Адрес: {obj['Адрес']}\n🔑 Код: {obj['Код']}")
            else:
                messages.append(f"❌ Объект с ID {tid} не найден.")

        return "\n\n".join(messages) if messages else "Нет данных."

    except Exception as e:
        logging.exception("Ошибка при загрузке данных")
        return f"Ошибка: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data_text = await fetch_user_data(user_id)
    await update.message.reply_text(data_text, reply_markup=get_refresh_button())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    new_data = await fetch_user_data(user_id)
    await query.edit_message_text(text=new_data, reply_markup=get_refresh_button())

def main():
    logging.basicConfig(level=logging.INFO)
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
