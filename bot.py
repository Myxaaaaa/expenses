import os
import re
import logging
import sys
import socket
import time
from datetime import datetime, timedelta
from html import escape
from dateutil import parser as date_parser
from dateutil import tz
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from database import Database
from typing import Optional

# Часовой пояс Бишкека (UTC+6)
BISHKEK_TZ = tz.gettz('Asia/Bishkek')

ALLOWED_ROLES = {
    "оператор",
    "администратор",
    "шеф"
}

DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S"
]

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация базы данных
db = Database()

# Токен бота: можно переопределить через переменную окружения BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN", "8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU")

# Настройки прокси для обхода блокировки Telegram API
# Поддерживаемые форматы:
# - HTTP прокси: http://username:password@proxy.example.com:8080
# - SOCKS5 прокси: socks5://username:password@proxy.example.com:1080
PROXY_URL = os.getenv("PROXY_URL", None)
PROXY_USERNAME = os.getenv("PROXY_USERNAME", None)
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", None)


def parse_expense(text: str, bot_username: str = None) -> tuple:
    """
    Парсит сообщение с расходом
    Форматы: "100 еда", "100 руб еда", "100 на еду", "100 - еда"
    """
    # Убираем упоминание бота если есть
    if bot_username:
        text = re.sub(rf'@?{re.escape(bot_username)}\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'@\w+\s*', '', text, flags=re.IGNORECASE)
    text = text.strip()
    
    # Ищем сумму (число с возможной точкой или запятой)
    amount_match = re.search(r'(\d+(?:[.,]\d+)?)', text)
    if not amount_match:
        return None, None
    
    amount_str = amount_match.group(1).replace(',', '.')
    try:
        amount = float(amount_str)
    except ValueError:
        return None, None
    

    description = re.sub(r'\d+(?:[.,]\d+)?\s*(?:руб|р|₽)?\s*[-на]?\s*', '', text, flags=re.IGNORECASE)
    description = description.strip()
    
    if not description:
        description = "Без описания"
    
    return amount, description


def parse_db_datetime(value):
    """Безопасно парсит дату из БД - считаем что время уже в часовом поясе Бишкека"""
    if isinstance(value, datetime):
        # Если дата уже datetime, но без часового пояса, добавляем Бишкекский
        # Время в БД хранится как Бишкекское время (без часового пояса)
        if value.tzinfo is None:
            value = value.replace(tzinfo=BISHKEK_TZ)
        return value
    if isinstance(value, str):
        # Пробуем разные форматы
        for fmt in DATETIME_FORMATS:
            try:
                dt = datetime.strptime(value, fmt)
                # Время в БД уже в часовом поясе Бишкека, просто добавляем tzinfo
                dt = dt.replace(tzinfo=BISHKEK_TZ)
                return dt
            except ValueError:
                continue
        # Пробуем ISO формат
        try:
            normalized = value.replace('Z', '+00:00')
            dt = datetime.fromisoformat(normalized)
            # Если дата без часового пояса, считаем что это Бишкекское время
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BISHKEK_TZ)
            return dt
        except ValueError:
            pass
    return get_bishkek_now()


def build_message_link(chat_id: int, chat_username: str, message_id: int) -> str:
    """Строит ссылку на сообщение в группе"""
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    chat_id_str = str(chat_id)
    if chat_id_str.startswith("-100"):
        chat_id_str = chat_id_str[4:]
    chat_id_str = chat_id_str.lstrip("-")
    return f"https://t.me/c/{chat_id_str}/{message_id}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    chat_info = (
        f"Тип чата: {update.message.chat.type}\n"
        f"Chat ID: {update.message.chat.id}"
    )
    await update.message.reply_text(
        "💰 Бот для учета расходов\n\n"
        "Как добавить расход:\n"
        "1. Ответьте на сообщение (или фото), к которому относится расход\n"
        "2. Напишите сумму с описанием\n"
        "3. Обязательно упомяните бота (@имябота)\n"
        "Пример: @bot 1500 продукты\n\n"
        "Команды:\n"
        "/expenses — расходы за сегодня\n"
        "/expenses_week — расходы за неделю\n"
        "/expenses_month — расходы за месяц\n"
        "/expenses_period 01.01.2024 07.01.2024 — расходы за период\n"
        "/delete <ID> — удалить свой расход\n"
        "/setrole <роль> — назначить роль (админ/шеф, по ответу или тегу)\n"
        "/setname <имя> — установить имя (по ответу или тегу)\n"
        "/roles — список ролей\n"
        "/info — информация о пользователе (по ответу/тегу) или всех\n"
        "/test — проверить работу бота\n\n"
        f"ℹ️ {chat_info}"
    )


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки работы бота"""
    chat_type = update.message.chat.type
    chat_id = update.message.chat.id
    user = update.message.from_user
    
    test_message = (
        f"✅ Бот работает!\n\n"
        f"📊 Информация:\n"
        f"Тип чата: {chat_type}\n"
        f"Chat ID: {chat_id}\n"
        f"Пользователь: {user.first_name} (@{user.username or 'нет username'})\n\n"
        f"💡 Попробуйте написать: 100 тест"
    )
    
    await update.message.reply_text(test_message)


def get_user_role_from_db(chat_id: int, user_id: int) -> Optional[str]:
    """Получает роль пользователя из БД"""
    role_result = db.get_role(chat_id, user_id)
    if role_result:
        return role_result[0]
    return None

async def set_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Назначает роль пользователю (только администраторы и шефы)"""
    chat_id = update.message.chat.id
    issuer_id = update.message.from_user.id
    
    # Проверяем роль из БД (приоритет) или статус в группе
    issuer_role = get_user_role_from_db(chat_id, issuer_id)
    if issuer_role not in ["администратор", "шеф"]:
        # Если нет роли в БД, проверяем статус в группе
        try:
            member = await context.bot.get_chat_member(chat_id, issuer_id)
            if member.status not in ["administrator", "creator"]:
                await update.message.reply_text("❌ Только администраторы и шефы могут назначать роли")
                return
        except:
            await update.message.reply_text("❌ Только администраторы и шефы могут назначать роли")
            return
    
    # Определяем целевого пользователя: из reply или из упоминания
    target_user = None
    role_parts = []
    mention_username = None
    
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        # Роль берем из аргументов команды
        if context.args:
            role_parts = context.args
    elif update.message.entities:
        # Ищем упоминания в сообщении
        text = update.message.text or ""
        
        for entity in update.message.entities:
            if entity.type == "text_mention":
                target_user = entity.user
                # Роль - это все аргументы, убираем упоминание
                mention_text = text[entity.offset:entity.offset+entity.length]
                if context.args:
                    args_text = " ".join(context.args)
                    role_text = args_text.replace(mention_text, "").strip()
                    role_parts = role_text.split() if role_text else []
                break
            elif entity.type == "mention":
                # Обычное упоминание @username
                mention_text = text[entity.offset:entity.offset+entity.length]
                mention_username = mention_text[1:]  # Убираем @
                # Роль - это все аргументы после упоминания
                if context.args:
                    args_text = " ".join(context.args)
                    role_text = args_text.replace(mention_text, "").strip()
                    role_parts = role_text.split() if role_text else []
                break
    
    # Если нашли упоминание @username, пытаемся найти пользователя
    if not target_user and mention_username:
        # Сначала пробуем найти в базе данных
        user_id_from_db = db.get_user_id_by_username(chat_id, mention_username)
        if user_id_from_db:
            try:
                # Если нашли user_id в БД, получаем информацию о пользователе
                member = await context.bot.get_chat_member(chat_id, user_id_from_db)
                target_user = member.user
            except Exception as e:
                logger.debug(f"Не удалось получить информацию о пользователе {user_id_from_db}: {e}")
        
        # Если не нашли в БД или не удалось получить информацию, пробуем через username напрямую
        # ВАЖНО: get_chat_member с username работает только если бот администратор
        # и пользователь находится в группе
        if not target_user and mention_username:
            clean_username = mention_username.lstrip('@')
            if clean_username:
                try:
                    # Пробуем найти через get_chat_member с username (работает если бот админ)
                    member = await context.bot.get_chat_member(chat_id, clean_username)
                    target_user = member.user
                except Exception as e:
                    # Игнорируем ошибку - это нормально, если бот не админ или пользователь не найден
                    logger.debug(f"Не удалось найти пользователя @{clean_username}: {e}")
                    # Не показываем ошибку пользователю, просто продолжаем
    
    if not target_user:
        if mention_username:
            await update.message.reply_text(
                f"❌ Не удалось найти пользователя @{mention_username}\n\n"
                "💡 Попробуйте:\n"
                "• Ответить на сообщение пользователя и написать /setrole роль\n"
                "• Убедитесь, что пользователь есть в группе\n"
                "• Если пользователь уже был добавлен ранее, попробуйте снова"
            )
        else:
            await update.message.reply_text(
                "↩️ Ответьте на сообщение участника или укажите тег, чтобы назначить роль\n"
                "Примеры:\n"
                "• /setrole оператор (в ответ на сообщение)\n"
                "• /setrole @username оператор"
            )
        return
    
    # Если роль не извлечена, берем из аргументов
    if not role_parts and context.args:
        # Фильтруем упоминания из аргументов
        role_parts = [arg for arg in context.args if not arg.startswith('@')]
    
    if not role_parts:
        await update.message.reply_text(
            "❌ Укажите роль. Доступно: оператор, администратор, шеф\n"
            "Пример: /setrole оператор (в ответ на сообщение или с упоминанием)"
        )
        return
    
    role = " ".join(role_parts).strip().lower()
    if role not in ALLOWED_ROLES:
        await update.message.reply_text(
            "❌ Неизвестная роль. Доступно: оператор, администратор, шеф"
        )
        return
    
    db.set_role(
        chat_id=chat_id,
        user_id=target_user.id,
        username=target_user.username or target_user.first_name or "Неизвестный",
        role=role,
        assigned_by=issuer_id
    )
    
    target_name = db.get_name(chat_id, target_user.id) or target_user.first_name
    await update.message.reply_text(
        f"✅ Роль назначена:\n"
        f"{target_name} — {role}"
    )


async def list_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список ролей в группе"""
    chat_id = update.message.chat.id
    roles = db.get_roles(chat_id)
    
    if not roles:
        await update.message.reply_text("ℹ️ Роли пока не назначены")
        return
    
    lines = ["👥 Роли участников:"]
    for user_id, username, role, assigned_at in roles:
        name = db.get_name(chat_id, user_id) or username or "Без имени"
        # assigned_at может быть строкой
        if isinstance(assigned_at, str):
            ts = assigned_at.split(".")[0]
        else:
            ts = assigned_at.strftime("%Y-%m-%d %H:%M")
        lines.append(f"• {name} — {role} (назначено {ts})")
    
    await update.message.reply_text("\n".join(lines))


async def set_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает имя пользователю (по ответу или тегу)"""
    chat_id = update.message.chat.id
    issuer_id = update.message.from_user.id
    
    # Определяем целевого пользователя: из reply или из упоминания
    target_user = None
    name_parts = []
    mention_username = None
    
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        # Имя берем из аргументов команды, фильтруя упоминания
        if context.args:
            name_parts = [arg for arg in context.args if not arg.startswith('@')]
    elif update.message.entities:
        # Ищем упоминания в сообщении
        text = update.message.text or ""
        
        for entity in update.message.entities:
            if entity.type == "text_mention":
                target_user = entity.user
                # Имя - это все аргументы, убираем упоминание
                mention_text = text[entity.offset:entity.offset+entity.length]
                if context.args:
                    args_text = " ".join(context.args)
                    name_text = args_text.replace(mention_text, "").strip()
                    name_parts = name_text.split() if name_text else []
                break
            elif entity.type == "mention":
                # Обычное упоминание @username
                mention_text = text[entity.offset:entity.offset+entity.length]
                mention_username = mention_text[1:]  # Убираем @
                # Имя - это все аргументы после упоминания
                if context.args:
                    args_text = " ".join(context.args)
                    name_text = args_text.replace(mention_text, "").strip()
                    name_parts = name_text.split() if name_text else []
                break
    
    # Если нашли упоминание @username, пытаемся найти пользователя
    if not target_user and mention_username:
        # Сначала пробуем найти в базе данных
        user_id_from_db = db.get_user_id_by_username(chat_id, mention_username)
        if user_id_from_db:
            try:
                # Если нашли user_id в БД, получаем информацию о пользователе
                member = await context.bot.get_chat_member(chat_id, user_id_from_db)
                target_user = member.user
            except Exception as e:
                logger.debug(f"Не удалось получить информацию о пользователе {user_id_from_db}: {e}")
        
        # Если не нашли в БД или не удалось получить информацию, пробуем через username напрямую
        # ВАЖНО: get_chat_member с username работает только если бот администратор
        # и пользователь находится в группе
        if not target_user and mention_username:
            clean_username = mention_username.lstrip('@')
            if clean_username:
                try:
                    # Пробуем найти через get_chat_member с username (работает если бот админ)
                    member = await context.bot.get_chat_member(chat_id, clean_username)
                    target_user = member.user
                except Exception as e:
                    # Игнорируем ошибку - это нормально, если бот не админ или пользователь не найден
                    logger.debug(f"Не удалось найти пользователя @{clean_username}: {e}")
                    # Не показываем ошибку пользователю, просто продолжаем
    
    if not target_user:
        if mention_username:
            await update.message.reply_text(
                f"❌ Не удалось найти пользователя @{mention_username}\n\n"
                "💡 Попробуйте:\n"
                "• Ответить на сообщение пользователя и написать /setname Имя\n"
                "• Убедитесь, что пользователь есть в группе\n"
                "• Если пользователь уже был добавлен ранее, попробуйте снова"
            )
        else:
            await update.message.reply_text(
                "↩️ Ответьте на сообщение участника или укажите тег, чтобы установить имя\n"
                "Примеры:\n"
                "• /setname Иван (в ответ на сообщение)\n"
                "• /setname @username Иван"
            )
        return
    
    # Если имя не извлечено из аргументов, берем все аргументы (фильтруя упоминания)
    if not name_parts and context.args:
        name_parts = [arg for arg in context.args if not arg.startswith('@')]
    
    if not name_parts:
        await update.message.reply_text(
            "❌ Укажите имя\n"
            "Примеры:\n"
            "• /setname Иван (в ответ на сообщение)\n"
            "• /setname @username Иван"
        )
        return
    
    name = " ".join(name_parts).strip()
    if not name:
        await update.message.reply_text("❌ Имя не может быть пустым")
        return
    
    db.set_name(
        chat_id=chat_id,
        user_id=target_user.id,
        username=target_user.username or target_user.first_name or "Неизвестный",
        name=name,
        assigned_by=issuer_id
    )
    
    await update.message.reply_text(
        f"✅ Имя установлено:\n"
        f"{target_user.first_name} → {name}"
    )


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию о пользователе или всех участниках"""
    chat_id = update.message.chat.id
    
    # Определяем целевого пользователя: из reply, упоминания или показываем всех
    target_user = None
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    elif update.message.entities:
        # Ищем упоминания в сообщении
        for entity in update.message.entities:
            if entity.type == "text_mention":
                target_user = entity.user
                break
    
    if target_user:
        # Информация о конкретном пользователе
        user_id = target_user.id
        role_result = db.get_role(chat_id, user_id)
        role = role_result[0] if role_result else "Нет роли"
        name = db.get_name(chat_id, user_id)
        
        info_text = (
            f"👤 Информация о пользователе:\n\n"
            f"Имя: {name or target_user.first_name or 'Не указано'}\n"
            f"Username: @{target_user.username or 'нет'}\n"
            f"Роль: {role}\n"
            f"ID: {user_id}"
        )
        await update.message.reply_text(info_text)
    else:
        # Общая информация о всех участниках
        all_info = db.get_all_info(chat_id)
        
        if not all_info:
            await update.message.reply_text("ℹ️ Информация о участниках отсутствует")
            return
        
        lines = ["👥 Информация о участниках:\n"]
        for user_id, username, role, name in all_info:
            # Формируем строку с именем и username
            name_part = name or "Без имени"
            username_part = f"@{username}" if username else "(нет username)"
            role_display = role or "Нет роли"
            
            # Объединяем: Имя (@username) — Роль
            lines.append(f"• {name_part} ({username_part}) — {role_display}")
        
        message_text = "\n".join(lines)
        # Разбиваем на части если слишком длинное
        if len(message_text) > 4096:
            # Отправляем частями
            current = ""
            for line in lines:
                if len(current + line) > 4096:
                    await update.message.reply_text(current)
                    current = line + "\n"
                else:
                    current += line + "\n"
            if current:
                await update.message.reply_text(current)
        else:
            await update.message.reply_text(message_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает сообщения с расходами"""
    try:
        if not update.message:
            logger.debug("Нет сообщения в update")
            return
        
        chat_type = update.message.chat.type
        logger.info(f"Получено сообщение, тип чата: {chat_type}, chat_id: {update.message.chat.id}")
        
        # Проверяем, что это сообщение в группе или супергруппе
        if chat_type not in ['group', 'supergroup']:
            logger.debug(f"Сообщение не из группы: {chat_type}")
            return
        
        # Сообщение должно быть ответом
        if not update.message.reply_to_message:
            logger.debug("Сообщение не является ответом — пропускаем")
            return
        
        # Получаем текст сообщения
        text = update.message.text
        if not text:
            logger.debug("Нет текста в сообщении")
            return
        
        logger.info(f"Получено сообщение в группе {update.message.chat.id}: {text}")
        
        # Получаем username бота для проверки упоминания
        bot_username = context.bot.username if context.bot and context.bot.username else None
        if bot_username:
            bot_username_lower = bot_username.lower()
            if f"@{bot_username_lower}" not in text.lower():
                logger.debug("Бот не упомянут — пропускаем сообщение")
                return
        else:
            logger.error("Не удалось получить username бота")
            return
        
        # Пропускаем команды
        if text.startswith('/'):
            return
        
        # Парсим расход
        amount, description = parse_expense(text, bot_username)
        
        if amount is None:
            logger.debug(f"Не удалось распарсить расход из: {text}")
            return
        
        logger.info(f"Распарсен расход: {amount} - {description}")
        
        # Добавляем в базу данных
        user = update.message.from_user
        username = user.username or user.first_name or "Неизвестный"
        
        original_message_id = update.message.reply_to_message.message_id
        
        try:
            # Используем текущее время в часовом поясе Бишкека
            current_time = get_bishkek_now()
            expense_id = db.add_expense(
                chat_id=update.message.chat.id,
                user_id=user.id,
                username=username,
                amount=amount,
                description=description,
                message_id=update.message.message_id,
                expense_date=current_time
            )
            logger.info(f"Расход добавлен в БД с ID: {expense_id}")
        except Exception as e:
            logger.error(f"Ошибка при добавлении расхода в БД: {e}")
            await update.message.reply_text(
                f"❌ Ошибка при сохранении расхода: {e}",
                reply_to_message_id=update.message.message_id
            )
            return
        
        # Отправляем подтверждение
        reply_text = (
            f"✅ Расход добавлен: {amount:.2f} сом - {description}\n"
            f"📎 Ответ на сообщение #{original_message_id}"
        )
        
        await update.message.reply_text(
            reply_text,
            reply_to_message_id=update.message.message_id
        )
    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}", exc_info=True)


async def show_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                       start_date: datetime = None, end_date: datetime = None):
    """Показывает все расходы за период"""
    chat_id = update.message.chat.id
    
    expenses = db.get_expenses(chat_id, start_date, end_date)
    total = db.get_total_amount(chat_id, start_date, end_date)
    
    if not expenses:
        period_text = ""
        if start_date and end_date:
            period_text = f" за период {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
        elif start_date:
            period_text = f" с {start_date.strftime('%d.%m.%Y')}"
        await update.message.reply_text(f"📭 Нет расходов{period_text}")
        return
    
    # Формируем сообщение
    message_parts = []
    
    if start_date and end_date:
        period_text = f"📅 Период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}\n"
    elif start_date:
        period_text = f"📅 С {start_date.strftime('%d.%m.%Y')}\n"
    else:
        period_text = ""
    
    header = f"{period_text}💰 Всего расходов: {len(expenses)}\n💵 Общая сумма: {total:.2f} сом\n\n"
    message_parts.append(escape(header))
    
    for expense in expenses:
        expense_id, user_id, username, amount, description, category, message_id, date_str = expense
        date_obj = parse_db_datetime(date_str)
        
        # Формируем ссылку на сообщение
        if message_id:
            chat_username = update.message.chat.username
            message_link = build_message_link(chat_id, chat_username, message_id)
            link_text = f'<a href="{message_link}">#{message_id}</a>'
        else:
            link_text = f"#{expense_id}"
        
        expense_text = (
            f"💸 {amount:.2f} сом - {escape(description)} (ID: {expense_id})\n"
            f"👤 {escape(username)} | {date_obj.strftime('%d.%m.%Y %H:%M')} | {link_text}\n"
        )
        message_parts.append(expense_text)
    
    # Разбиваем на части если сообщение слишком длинное
    full_message = "\n".join(message_parts)
    if len(full_message) > 4096:
        # Отправляем общую статистику
        await update.message.reply_text(
            escape(f"{period_text}💰 Всего расходов: {len(expenses)}\n💵 Общая сумма: {total:.2f} сом"),
            parse_mode='HTML'
        )
        
        # Отправляем расходы частями
        current_message = ""
        for expense in expenses:
            expense_id, user_id, username, amount, description, category, message_id, date_str = expense
            date_obj = parse_db_datetime(date_str)
            
            if message_id:
                chat_username = update.message.chat.username
                message_link = build_message_link(chat_id, chat_username, message_id)
                link_text = f'<a href="{message_link}">#{message_id}</a>'
            else:
                link_text = f"#{expense_id}"
            
            expense_text = (
                f"💸 {amount:.2f} сом - {escape(description)} (ID: {expense_id})\n"
                f"👤 {escape(username)} | {date_obj.strftime('%d.%m.%Y %H:%M')} | {link_text}\n\n"
            )
            
            if len(current_message + expense_text) > 4096:
                await update.message.reply_text(current_message, parse_mode='HTML')
                current_message = expense_text
            else:
                current_message += expense_text
        
        if current_message:
            await update.message.reply_text(current_message, parse_mode='HTML')
    else:
        await update.message.reply_text(full_message, parse_mode='HTML')


def get_bishkek_now():
    """Получает текущее время в часовом поясе Бишкека"""
    return datetime.now(BISHKEK_TZ)

def get_bishkek_today():
    """Получает начало сегодняшнего дня в часовом поясе Бишкека"""
    now = get_bishkek_now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def check_network_connectivity():
    """Проверяет возможность подключения к Telegram API"""
    try:
        # Пробуем подключиться к api.telegram.org
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex(('api.telegram.org', 443))
        sock.close()
        return result == 0
    except Exception as e:
        logger.warning(f"Ошибка при проверке подключения: {e}")
        return False


def create_request_with_proxy():
    """Создает Request объект с настройками прокси из переменных окружения"""
    if not PROXY_URL:
        return None
    
    try:
        # Если указаны отдельные username и password, добавляем их в URL
        proxy_url = PROXY_URL
        if PROXY_USERNAME and PROXY_PASSWORD:
            # Извлекаем схему, хост и порт из URL
            if '://' in proxy_url:
                scheme, rest = proxy_url.split('://', 1)
                if '@' not in rest:  # Если уже нет авторизации
                    proxy_url = f"{scheme}://{PROXY_USERNAME}:{PROXY_PASSWORD}@{rest}"
        
        logger.info(f"Используется прокси: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")
        return HTTPXRequest(proxy=proxy_url)
    except Exception as e:
        logger.error(f"Ошибка при настройке прокси: {e}")
        print(f"⚠️  Предупреждение: Не удалось настроить прокси: {e}")
        return None

async def expenses_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает расходы за сегодня (только после 00:00 по времени Бишкека)"""
    # Получаем начало сегодняшнего дня (00:00:00) в часовом поясе Бишкека
    today_start = get_bishkek_today()
    # Конец сегодняшнего дня (23:59:59.999999)
    today_end = today_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    await show_expenses(update, context, start_date=today_start, end_date=today_end)


async def expenses_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает расходы за неделю"""
    today = get_bishkek_today()
    week_ago = today - timedelta(days=7)
    end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
    await show_expenses(update, context, start_date=week_ago, end_date=end)


async def expenses_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает расходы за месяц"""
    today = get_bishkek_today()
    month_ago = today - timedelta(days=30)
    end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
    await show_expenses(update, context, start_date=month_ago, end_date=end)


async def expenses_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает расходы за период"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📅 Укажите период в формате:\n"
            "/expenses_period 01.01.2024 31.01.2024\n"
            "или\n"
            "/expenses_period 2024-01-01 2024-01-31"
        )
        return
    
    try:
        start_date = date_parser.parse(context.args[0], dayfirst=True)
        end_date = date_parser.parse(context.args[1], dayfirst=True)
        # Устанавливаем часовой пояс Бишкека
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=BISHKEK_TZ)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=BISHKEK_TZ)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        await show_expenses(update, context, start_date=start_date, end_date=end_date)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка парсинга даты: {e}")


async def delete_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет расход по ID (администраторы и шефы могут удалять чужие расходы)"""
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ Укажите ID расхода для удаления\n"
            "Пример: /delete 5\n\n"
            "ID можно найти в списке расходов (команда /expenses)"
        )
        return
    
    try:
        expense_id = int(context.args[0])
        chat_id = update.message.chat.id
        user_id = update.message.from_user.id
        
        # Проверяем, существует ли расход
        expense = db.get_expense_by_id(expense_id, chat_id)
        if not expense:
            await update.message.reply_text(f"❌ Расход с ID {expense_id} не найден")
            return
        
        expense_owner_id = expense[1]  # user_id владельца расхода
        
        # Проверяем роль пользователя
        user_role = get_user_role_from_db(chat_id, user_id)
        
        # Администраторы и шефы могут удалять любые расходы
        # Обычные пользователи могут удалять только свои расходы
        can_delete_any = user_role in ["администратор", "шеф"]
        
        if not can_delete_any and expense_owner_id != user_id:
            await update.message.reply_text(
                f"❌ Вы можете удалять только свои расходы.\n"
                f"Только администраторы и шефы могут удалять чужие расходы."
            )
            return
        
        # Удаляем расход (если администратор/шеф - без проверки user_id, иначе - с проверкой)
        deleted = db.delete_expense(
            expense_id, 
            chat_id, 
            user_id if not can_delete_any else None,
            force=can_delete_any
        )
        
        if deleted:
            expense_amount, expense_desc = expense[3], expense[4]
            owner_username = expense[2] or "Неизвестный"
            owner_name = db.get_name(chat_id, expense_owner_id) or owner_username
            
            if can_delete_any and expense_owner_id != user_id:
                await update.message.reply_text(
                    f"✅ Расход удален администратором:\n"
                    f"💸 {expense_amount:.2f} сом - {expense_desc}\n"
                    f"👤 Автор: {owner_name}"
                )
            else:
                await update.message.reply_text(
                    f"✅ Расход удален:\n"
                    f"💸 {expense_amount:.2f} сом - {expense_desc}"
                )
        else:
            await update.message.reply_text(
                f"❌ Не удалось удалить расход.\n"
                f"Возможно, расход уже был удален или произошла ошибка."
            )
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом")
    except Exception as e:
        logger.error(f"Ошибка при удалении расхода: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка при удалении: {e}")


def main():
    """Запуск бота"""
    if not BOT_TOKEN:
        print("❌ Ошибка: BOT_TOKEN не установлен!")
        print("Установите переменную окружения BOT_TOKEN или добавьте токен в код")
        return
    
    # Настраиваем прокси если указан
    request = create_request_with_proxy()
    
    # Проверяем сетевое подключение (пропускаем, если используется прокси)
    if not PROXY_URL:
        print("Проверка подключения к интернету...")
        if not check_network_connectivity():
            print("\n❌ ОШИБКА: Не удалось подключиться к Telegram API")
            print("\nВозможные причины:")
            print("1. Нет подключения к интернету")
            print("2. Проблемы с DNS (попробуйте изменить DNS на 8.8.8.8 или 1.1.1.1)")
            print("3. Файрвол блокирует подключение к api.telegram.org")
            print("4. Требуется прокси-сервер (настройте через переменные окружения)")
            print("\nДля настройки прокси установите переменную окружения:")
            print("  PROXY_URL=socks5://proxy.example.com:1080")
            print("  PROXY_USERNAME=username  (опционально)")
            print("  PROXY_PASSWORD=password  (опционально)")
            print("\nПроверьте интернет-соединение и попробуйте снова.")
            sys.exit(1)
        print("✓ Подключение установлено")
    else:
        if request:
            print("✓ Прокси настроен")
        else:
            print("⚠️  Прокси указан, но не удалось настроить. Попробую подключиться напрямую...")
    
    # Создаем приложение
    try:
        builder = Application.builder().token(BOT_TOKEN)
        if request:
            builder = builder.request(request)
        application = builder.build()
    except Exception as e:
        print(f"\n❌ Ошибка при создании приложения: {e}")
        print("Проверьте правильность токена бота и настройки прокси (если используется).")
        sys.exit(1)
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test))
    application.add_handler(CommandHandler("expenses", expenses_today))
    application.add_handler(CommandHandler("expenses_week", expenses_week))
    application.add_handler(CommandHandler("expenses_month", expenses_month))
    application.add_handler(CommandHandler("expenses_period", expenses_period))
    application.add_handler(CommandHandler("delete", delete_expense))
    application.add_handler(CommandHandler("setrole", set_role))
    application.add_handler(CommandHandler("roles", list_roles))
    application.add_handler(CommandHandler("setname", set_name))
    application.add_handler(CommandHandler("info", info))
    
    # Обработчик всех текстовых сообщений (включая ответы)
    # Обрабатываем сообщения с текстом или ответы на сообщения
    # Убираем фильтр COMMAND, чтобы обрабатывать все сообщения в группах
    message_filter = filters.ChatType.GROUPS & (filters.TEXT | filters.REPLY)
    application.add_handler(MessageHandler(message_filter, handle_message))
    
    # Также добавляем обработчик для всех сообщений в группах для отладки
    async def debug_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message and update.message.chat.type in ['group', 'supergroup']:
            logger.info(f"DEBUG: Получено сообщение в группе {update.message.chat.id}, тип: {update.message.chat.type}, текст: {update.message.text}")
    
    # Раскомментируйте следующую строку для отладки:
    # application.add_handler(MessageHandler(filters.ChatType.GROUPS, debug_handler))
    
    # Запускаем бота с обработкой ошибок
    logger.info("Бот запущен...")
    try:
        print("Бот запущен...")
        print("Бот будет обрабатывать сообщения с расходами в группах")
        print("Примеры: '100 еда', '500 на такси', '1500 - продукты'")
    except UnicodeEncodeError:
        # Для Windows консоли без поддержки UTF-8
        print("Bot started...")
        print("Bot will process expense messages in groups")
        print("Examples: '100 food', '500 taxi', '1500 - products'")
    
    # Запуск с обработкой сетевых ошибок
    max_retries = 5
    retry_count = 0
    while retry_count < max_retries:
        try:
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            break  # Успешный запуск, выходим из цикла
        except (NetworkError, TimedOut) as e:
            retry_count += 1
            error_msg = str(e)
            logger.error(f"Сетевая ошибка (попытка {retry_count}/{max_retries}): {error_msg}")
            
            if retry_count < max_retries:
                wait_time = retry_count * 5  # Увеличиваем время ожидания
                print(f"\n⚠️  Сетевая ошибка. Повторная попытка через {wait_time} секунд...")
                print(f"Ошибка: {error_msg}")
                time.sleep(wait_time)
            else:
                print("\n❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к Telegram API")
                print(f"\nОшибка: {error_msg}")
                print("\nВозможные решения:")
                print("1. Проверьте подключение к интернету")
                print("2. Проверьте настройки файрвола/антивируса")
                print("3. Попробуйте изменить DNS серверы (8.8.8.8, 1.1.1.1)")
                print("4. Если используете VPN/прокси, убедитесь что он работает")
                print("5. Подождите несколько минут и попробуйте запустить бота снова")
                sys.exit(1)
        except KeyboardInterrupt:
            print("\n\n⏹️  Остановка бота...")
            logger.info("Бот остановлен пользователем")
            break
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}", exc_info=True)
            print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
            print("Проверьте логи для получения подробной информации.")
            sys.exit(1)


if __name__ == "__main__":
    main()

