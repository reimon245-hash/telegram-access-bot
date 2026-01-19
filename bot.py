import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === СЮДА ВСТАВЬТЕ СВОЙ ТОКЕН ОТ @BotFather ===
TELEGRAM_TOKEN = "8449612137:AAHQbG_bFwirLe16_Ib2y3upCjEys1GR6-0"

# === ИМЯ ВАШЕЙ GOOGLE-ТАБЛИЦЫ ===
GOOGLE_SHEET_NAME = "teleg-bot-passw"

# === ИМЯ ЛИСТА В ТАБЛИЦЕ (обычно "Лист1") ===
WORKSHEET_NAME = "page1"

# === ФАЙЛ ДОСТУПА (лежит в этой же папке) ===
CREDENTIALS_PATH = "credentials.json"

# 🔴 НОВОЕ: функция для разбора диапазонов (например: "1-3,5,7-9" → [1,2,3,5,7,8,9])
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
                else:
                    # Если написали "5-3", игнорируем или можно обработать как ошибку
                    pass
            except ValueError:
                continue  # некорректный формат — пропускаем
        else:
            try:
                ids.add(int(part))
            except ValueError:
                continue  # не число — пропускаем
    return sorted(ids)

def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scopes=scope)
    client = gspread.authorize(creds)
    sheet = client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)
    return sheet

def get_refresh_button():
    keyboard = [[InlineKeyboardButton("ОБНОВИТЬ КОДЫ", callback_data="refresh")]]
    return InlineKeyboardMarkup(keyboard)

async def fetch_user_data(user_id: str):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()

        user_row = None
        for row in records:
            if str(row.get("ДОСТУП", "")).strip() == user_id:
                user_row = row
                break

        if not user_row:
            return "У вас нет доступа."

        info_str = str(user_row.get("ИНФОРМАЦИЯ", "")).strip()
        if not info_str:
            return "Нет данных для отображения."

        # 🔴 ИСПОЛЬЗУЕМ НОВУЮ ФУНКЦИЮ ДЛЯ РАЗБОРА ДИАПАЗОНОВ
        target_ids = parse_id_ranges(info_str)

        if not target_ids:
            return "Не удалось распознать ID или диапазоны."

        all_objects = {}
        for row in records:
            try:
                obj_id = int(row["ID"])
                all_objects[obj_id] = {
                    "Адрес": row.get("Адрес", ""),
                    "Код": row.get("Код", "")
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
    print("✅ Бот запущен! Напишите ему в Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()