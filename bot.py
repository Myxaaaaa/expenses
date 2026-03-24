import os

import re

import logging

import sys

import socket

import time

import uuid

import signal

import atexit

import io

import json

import difflib

from datetime import datetime, timedelta

from html import escape

from dateutil import parser as date_parser

from dateutil import tz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup

from telegram.error import NetworkError, TimedOut, Conflict

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from telegram.request import HTTPXRequest

from database import Database

from typing import Optional

try:

    from database_postgres import PostgresDatabase

except ModuleNotFoundError:

    PostgresDatabase = None

BISHKEK_TZ = tz.gettz('Asia/Bishkek')

DAILY_EXPENSE_LIMIT = 500_000

ALLOWED_ROLES = {

    "оператор",

    "администратор",

    "шеф"

}

DATETIME_FORMATS = [

    "%Y-%m-%d %H:%M:%S.%f",

    "%Y-%m-%d %H:%M:%S"

]

logging.basicConfig(

    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',

    level=logging.INFO,

    handlers=[

        logging.FileHandler('bot.log', encoding='utf-8'),

        logging.StreamHandler(sys.stdout)

    ]

)

logger = logging.getLogger(__name__)

if os.getenv("DATABASE_URL") and PostgresDatabase is not None:

    db = PostgresDatabase()

else:

    db = Database()

DEFAULT_TOKEN = "8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU"

ENV_TOKEN = os.getenv("BOT_TOKEN", None)

if ENV_TOKEN and ENV_TOKEN.startswith("8137903259"):

    BOT_TOKEN = ENV_TOKEN

    logger.info("Используется токен из переменной окружения")

else:

    BOT_TOKEN = DEFAULT_TOKEN

    if ENV_TOKEN:

        logger.warning(f"Переменная окружения BOT_TOKEN установлена, но использует другой токен. Используется токен из кода.")

    else:

        logger.info("Используется токен из кода")

PROXY_URL = os.getenv("PROXY_URL", None)

PROXY_USERNAME = os.getenv("PROXY_USERNAME", None)

PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", None)

DATA_DIR = os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "."

try:

    os.makedirs(DATA_DIR, exist_ok=True)

except Exception:

    DATA_DIR = "."

EXPENSES_JSONL_PATH = os.path.join(DATA_DIR, "expenses_log.jsonl")

def parse_expense(text: str, bot_username: str = None) -> tuple:

    if bot_username:

        text = re.sub(rf'@?{re.escape(bot_username)}\s*', '', text, flags=re.IGNORECASE)

    text = re.sub(r'@\w+\s*', '', text, flags=re.IGNORECASE)

    text = text.strip()

    amount_match = re.search(r'(\d+(?:[.,]\d+)?)', text)

    if not amount_match:

        return None, None

    amount_str = amount_match.group(1).replace(',', '.')

    try:

        amount = float(amount_str)

    except ValueError:

        return None, None

    description = re.sub(

        r'^\s*\d+(?:[.,]\d+)?\s*(?:руб|р|₽)?\s*(?:(?:-\s*)|(?:на\s+))?',

        '',

        text,

        flags=re.IGNORECASE,

    )

    description = description.strip()

    if not description:

        description = "Без описания"

    return amount, description

def normalize_description(description: str) -> str:

    return re.sub(r"\s+", " ", (description or "").strip().lower())

def find_similar_today_descriptions(

    chat_id: int,

    target_description: str,

    start_date: datetime,

    end_date: datetime,

    max_suggestions: int = 3,

) -> list[str]:

    target_norm = normalize_description(target_description)

    if not target_norm:

        return []

    expenses_today = db.get_expenses(chat_id, start_date, end_date)

    unique_by_norm = {}

    for exp in expenses_today:

        original_desc = (exp[4] or "").strip()

        norm_desc = normalize_description(original_desc)

        if not norm_desc or norm_desc == target_norm:

            continue

        if norm_desc not in unique_by_norm:

            unique_by_norm[norm_desc] = original_desc

    scored = []

    for norm_desc, original_desc in unique_by_norm.items():

        similarity = difflib.SequenceMatcher(None, target_norm, norm_desc).ratio()

        if similarity >= 0.78:

            scored.append((similarity, original_desc))

    scored.sort(key=lambda item: item[0], reverse=True)

    return [desc for _, desc in scored[:max_suggestions]]

def _expense_name_suggestions_keyboard(

    original_description: str,

    suggestions: list[str],

) -> InlineKeyboardMarkup:

    rows = []

    for idx, suggested in enumerate(suggestions):

        rows.append([

            InlineKeyboardButton(

                f"✅ {suggested}",

                callback_data=f"exp_suggest_use:{idx}",

            )

        ])

    rows.append([InlineKeyboardButton("✍️ Оставить как есть", callback_data="exp_suggest_keep")])

    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="exp_suggest_cancel")])

    return InlineKeyboardMarkup(rows)

async def finalize_expense_add(

    update: Update,

    context: ContextTypes.DEFAULT_TYPE,

    *,

    amount: float,

    description: str,

) -> None:

    user = update.message.from_user

    username = user.username or user.first_name or "Неизвестный"

    original_message_id = update.message.reply_to_message.message_id

    try:

        current_time = get_bishkek_now()

        daily_limit = get_chat_daily_limit(update.message.chat.id)

        today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

        today_end = current_time.replace(hour=23, minute=59, second=59, microsecond=999999)

        previous_total_today = db.get_total_amount(

            chat_id=update.message.chat.id,

            start_date=today_start,

            end_date=today_end

        )

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

        new_total_today = (previous_total_today or 0) + amount

        record = {

            "id": expense_id,

            "chat_id": update.message.chat.id,

            "user_id": user.id,

            "username": username,

            "amount": amount,

            "description": description,

            "message_id": update.message.message_id,

            "original_message_id": original_message_id,

            "created_at": current_time.isoformat(),

        }

        try:

            with open(EXPENSES_JSONL_PATH, "a", encoding="utf-8") as f:

                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        except Exception as log_err:

            logger.error(f"Не удалось записать расход в JSONL: {log_err}", exc_info=True)

    except Exception as e:

        logger.error(f"Ошибка при добавлении расхода в БД: {e}")

        await update.message.reply_text(

            f"❌ Ошибка при сохранении расхода: {e}",

            reply_to_message_id=update.message.message_id

        )

        return

    user_msg_id = update.message.message_id

    reply_text = (

        f"✅ Расход добавлен (ID: {expense_id})\n"

        f"💸 {amount:.2f} сом — {escape(description)}\n"

        f"📎 Ответ на сообщение #{original_message_id}"

    )

    confirm_keyboard = _expense_actions_keyboard(expense_id, user_msg_id)

    await update.message.reply_text(

        reply_text,

        reply_to_message_id=user_msg_id,

        reply_markup=confirm_keyboard,

        parse_mode="HTML"

    )

    try:

        if previous_total_today <= daily_limit and new_total_today > daily_limit:

            warning_text = (

                "🚨 ПРЕВЫШЕН ДНЕВНОЙ ЛИМИТ ПО РАСХОДАМ!\n"

                f"💵 Общая сумма за сегодня: {new_total_today:.2f} сом\n"

                f"📊 Установленный лимит: {daily_limit:.2f} сом"

            )

            await context.bot.send_message(

                chat_id=update.message.chat.id,

                text=warning_text

            )

    except Exception as warn_err:

        logger.error(f"Ошибка при отправке предупреждения о превышении лимита: {warn_err}", exc_info=True)

def parse_db_datetime(value):

    if isinstance(value, datetime):

        if value.tzinfo is None:

            value = value.replace(tzinfo=BISHKEK_TZ)

        return value

    if isinstance(value, str):

        for fmt in DATETIME_FORMATS:

            try:

                dt = datetime.strptime(value, fmt)

                dt = dt.replace(tzinfo=BISHKEK_TZ)

                return dt

            except ValueError:

                continue

        try:

            normalized = value.replace('Z', '+00:00')

            dt = datetime.fromisoformat(normalized)

            if dt.tzinfo is None:

                dt = dt.replace(tzinfo=BISHKEK_TZ)

            return dt

        except ValueError:

            pass

    return get_bishkek_now()

def build_message_link(chat_id: int, chat_username: str, message_id: int) -> str:

    if chat_username:

        return f"https://t.me/{chat_username}/{message_id}"

    chat_id_str = str(chat_id)

    if chat_id_str.startswith("-100"):

        chat_id_str = chat_id_str[4:]

    chat_id_str = chat_id_str.lstrip("-")

    return f"https://t.me/c/{chat_id_str}/{message_id}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        logger.info(f"Получена команда /start от пользователя {update.message.from_user.id} в чате {update.message.chat.id}")

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

        await _delete_command_message(update)

    except Exception as e:

        logger.error(f"Ошибка в команде /start: {e}", exc_info=True)

        try:

            await update.message.reply_text(f"❌ Ошибка: {e}")

            await _delete_command_message(update)

        except:

            pass

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        logger.info(f"Получена команда /test от пользователя {update.message.from_user.id} в чате {update.message.chat.id}")

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

        await _delete_command_message(update)

    except Exception as e:

        logger.error(f"Ошибка в команде /test: {e}", exc_info=True)

        try:

            await update.message.reply_text(f"❌ Ошибка: {e}")

            await _delete_command_message(update)

        except:

            pass

def get_user_role_from_db(chat_id: int, user_id: int) -> Optional[str]:

    role_result = db.get_role(chat_id, user_id)

    if role_result:

        return role_result[0]

    return None

def get_chat_daily_limit(chat_id: int) -> float:

    try:

        limit = db.get_daily_limit(chat_id)

        if limit is None:

            return float(DAILY_EXPENSE_LIMIT)

        return float(limit)

    except Exception as e:

        logger.error(f"Ошибка при получении дневного лимита для чата {chat_id}: {e}", exc_info=True)

        return float(DAILY_EXPENSE_LIMIT)

async def set_role(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.message.chat.id

    issuer_id = update.message.from_user.id

    issuer_role = get_user_role_from_db(chat_id, issuer_id)

    if issuer_role not in ["администратор", "шеф"]:

        try:

            member = await context.bot.get_chat_member(chat_id, issuer_id)

            if member.status not in ["administrator", "creator"]:

                await update.message.reply_text("❌ Только администраторы и шефы могут назначать роли")

                await _delete_command_message(update)

                return

        except:

            await update.message.reply_text("❌ Только администраторы и шефы могут назначать роли")

            await _delete_command_message(update)

            return

    target_user = None

    role_parts = []

    mention_username = None

    if update.message.reply_to_message:

        target_user = update.message.reply_to_message.from_user

        if context.args:

            role_parts = context.args

    elif update.message.entities:

        msg = update.message

        text = msg.text or ""

        for entity in update.message.entities:

            if entity.type == "text_mention":

                target_user = entity.user

                try:

                    mention_text = msg.parse_entity(entity) or text[entity.offset : entity.offset + entity.length]

                except Exception:

                    mention_text = text[entity.offset : entity.offset + entity.length]

                if context.args:

                    args_text = " ".join(context.args)

                    role_text = args_text.replace(mention_text, "").strip()

                    role_parts = role_text.split() if role_text else []

                break

            elif entity.type == "mention":

                try:

                    mention_text = msg.parse_entity(entity) or ""

                except Exception:

                    mention_text = text[entity.offset : entity.offset + entity.length] if entity.offset is not None else ""

                mention_username = (mention_text or "").lstrip("@")

                if context.args:

                    args_text = " ".join(context.args)

                    role_text = args_text.replace(mention_text, "").strip()

                    role_parts = role_text.split() if role_text else []

                break

    if not mention_username and context.args:

        for arg in context.args:

            if arg.startswith("@"):

                mention_username = arg.lstrip("@").strip()

                if not role_parts and context.args:

                    role_parts = [a for a in context.args if not a.startswith("@")]

                break

    if not target_user and mention_username:

        user_id_from_db = db.get_user_id_by_username(chat_id, mention_username)

        if user_id_from_db:

            try:

                member = await context.bot.get_chat_member(chat_id, user_id_from_db)

                target_user = member.user

            except Exception as e:

                logger.debug(f"Не удалось получить информацию о пользователе {user_id_from_db}: {e}")

        if not target_user and mention_username:

            clean_username = mention_username.lstrip('@')

            if clean_username:

                try:

                    member = await context.bot.get_chat_member(chat_id, clean_username)

                    target_user = member.user

                except Exception as e:

                    logger.debug(f"Не удалось найти пользователя @{clean_username}: {e}")

    if not target_user:

        if mention_username:

            await update.message.reply_text(

                f"❌ Не удалось найти пользователя @{mention_username}\n\n"

                "💡 Попробуйте:\n"

                "• Ответить на сообщение пользователя и написать /setrole роль\n"

                "• Убедитесь, что пользователь есть в группе и бот — администратор\n"

                "• Если пользователь уже был добавлен ранее, попробуйте снова"

            )

        else:

            await update.message.reply_text(

                "↩️ Ответьте на сообщение участника или укажите тег @username, чтобы назначить роль\n"

                "Примеры:\n"

                "• /setrole оператор (в ответ на сообщение)\n"

                "• /setrole @username оператор"

            )

        await _delete_command_message(update)

        return

    if not role_parts and context.args:

        role_parts = [arg for arg in context.args if not arg.startswith('@')]

    if not role_parts:

        await update.message.reply_text(

            "❌ Укажите роль. Доступно: оператор, администратор, шеф\n"

            "Пример: /setrole оператор (в ответ на сообщение или с упоминанием)"

        )

        await _delete_command_message(update)

        return

    role = " ".join(role_parts).strip().lower()

    if role not in ALLOWED_ROLES:

        await update.message.reply_text(

            "❌ Неизвестная роль. Доступно: оператор, администратор, шеф"

        )

        await _delete_command_message(update)

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

    await _delete_command_message(update)

async def list_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.message.chat.id

    roles = db.get_roles(chat_id)

    if not roles:

        await update.message.reply_text("ℹ️ Роли пока не назначены")

        await _delete_command_message(update)

        return

    lines = ["👥 Роли участников:"]

    for user_id, username, role, assigned_at in roles:

        name = db.get_name(chat_id, user_id) or username or "Без имени"

        if isinstance(assigned_at, str):

            ts = assigned_at.split(".")[0]

        else:

            ts = assigned_at.strftime("%Y-%m-%d %H:%M")

        lines.append(f"• {name} — {role} (назначено {ts})")

    await update.message.reply_text("\n".join(lines))

    await _delete_command_message(update)

async def set_name(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.message.chat.id

    issuer_id = update.message.from_user.id

    target_user = None

    name_parts = []

    mention_username = None

    if update.message.reply_to_message:

        target_user = update.message.reply_to_message.from_user

        if context.args:

            name_parts = [arg for arg in context.args if not arg.startswith('@')]

    elif update.message.entities:

        msg = update.message

        text = msg.text or ""

        for entity in update.message.entities:

            if entity.type == "text_mention":

                target_user = entity.user

                try:

                    mention_text = msg.parse_entity(entity) or text[entity.offset : entity.offset + entity.length]

                except Exception:

                    mention_text = text[entity.offset : entity.offset + entity.length]

                if context.args:

                    args_text = " ".join(context.args)

                    name_text = args_text.replace(mention_text, "").strip()

                    name_parts = name_text.split() if name_text else []

                break

            elif entity.type == "mention":

                try:

                    mention_text = msg.parse_entity(entity) or ""

                except Exception:

                    mention_text = text[entity.offset : entity.offset + entity.length] if entity.offset is not None else ""

                mention_username = (mention_text or "").lstrip("@")

                if context.args:

                    args_text = " ".join(context.args)

                    name_text = args_text.replace(mention_text, "").strip()

                    name_parts = name_text.split() if name_text else []

                break

    if not mention_username and context.args:

        for arg in context.args:

            if arg.startswith("@"):

                mention_username = arg.lstrip("@").strip()

                if not name_parts and context.args:

                    name_parts = [a for a in context.args if not a.startswith("@")]

                break

    if not target_user and mention_username:

        user_id_from_db = db.get_user_id_by_username(chat_id, mention_username)

        if user_id_from_db:

            try:

                member = await context.bot.get_chat_member(chat_id, user_id_from_db)

                target_user = member.user

            except Exception as e:

                logger.debug(f"Не удалось получить информацию о пользователе {user_id_from_db}: {e}")

        if not target_user and mention_username:

            clean_username = mention_username.lstrip('@')

            if clean_username:

                try:

                    member = await context.bot.get_chat_member(chat_id, clean_username)

                    target_user = member.user

                except Exception as e:

                    logger.debug(f"Не удалось найти пользователя @{clean_username}: {e}")

    if not target_user:

        if mention_username:

            await update.message.reply_text(

                f"❌ Не удалось найти пользователя @{mention_username}\n\n"

                "💡 Попробуйте:\n"

                "• Ответить на сообщение пользователя и написать /setname Имя\n"

                "• Убедитесь, что пользователь есть в группе и бот — администратор\n"

                "• Если пользователь уже был добавлен ранее, попробуйте снова"

            )

        else:

            await update.message.reply_text(

                "↩️ Ответьте на сообщение участника или укажите тег @username\n"

                "Примеры:\n"

                "• /setname Иван (в ответ на сообщение)\n"

                "• /setname @username Иван"

            )

        await _delete_command_message(update)

        return

    if not name_parts and context.args:

        name_parts = [arg for arg in context.args if not arg.startswith('@')]

    if not name_parts:

        await update.message.reply_text(

            "❌ Укажите имя\n"

            "Примеры:\n"

            "• /setname Иван (в ответ на сообщение)\n"

            "• /setname @username Иван"

        )

        await _delete_command_message(update)

        return

    name = " ".join(name_parts).strip()

    if not name:

        await update.message.reply_text("❌ Имя не может быть пустым")

        await _delete_command_message(update)

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

    await _delete_command_message(update)

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.message.chat.id

    target_user = None

    if update.message.reply_to_message:

        target_user = update.message.reply_to_message.from_user

    elif update.message.entities:

        for entity in update.message.entities:

            if entity.type == "text_mention":

                target_user = entity.user

                break

            if entity.type == "mention":

                msg = update.message

                try:

                    mention_text = msg.parse_entity(entity) or ""

                except Exception:

                    text = msg.text or ""

                    mention_text = text[entity.offset : entity.offset + entity.length] if entity.offset is not None else ""

                mention_username = (mention_text or "").lstrip("@")

                if mention_username:

                    uid = db.get_user_id_by_username(chat_id, mention_username)

                    if uid:

                        try:

                            member = await context.bot.get_chat_member(chat_id, uid)

                            target_user = member.user

                        except Exception:

                            pass

                    if not target_user and mention_username:

                        try:

                            member = await context.bot.get_chat_member(chat_id, mention_username)

                            target_user = member.user

                        except Exception:

                            pass

                break

    if not target_user and context.args and context.args[0].startswith("@"):

        mention_username = context.args[0].lstrip("@").strip()

        if mention_username:

            uid = db.get_user_id_by_username(chat_id, mention_username)

            if uid:

                try:

                    member = await context.bot.get_chat_member(chat_id, uid)

                    target_user = member.user

                except Exception:

                    pass

            if not target_user:

                try:

                    member = await context.bot.get_chat_member(chat_id, mention_username)

                    target_user = member.user

                except Exception:

                    pass

    if target_user:

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

        await _delete_command_message(update)

    else:

        all_info = db.get_all_info(chat_id)

        if not all_info:

            await update.message.reply_text("ℹ️ Информация о участниках отсутствует")

            await _delete_command_message(update)

            return

        lines = ["👥 Информация о участниках:\n"]

        for user_id, username, role, name in all_info:

            name_part = name or "Без имени"

            username_part = f"@{username}" if username else "(нет username)"

            role_display = role or "Нет роли"

            lines.append(f"• {name_part} ({username_part}) — {role_display}")

        message_text = "\n".join(lines)

        if len(message_text) > 4096:

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

    await _delete_command_message(update)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        if not update.message:

            logger.debug("Нет сообщения в update")

            return

        chat_type = update.message.chat.type

        logger.info(f"Получено сообщение, тип чата: {chat_type}, chat_id: {update.message.chat.id}")

        if chat_type not in ['group', 'supergroup']:

            logger.debug(f"Сообщение не из группы: {chat_type}")

            return

        if update.message.text and context.user_data.get("exp_edit"):

            await handle_expense_edit_message(update, context)

            return

        if update.message.message_id == context.user_data.pop("_last_edit_message_id", None):

            return

        if not update.message.reply_to_message:

            logger.debug("Сообщение не является ответом — пропускаем")

            return

        text = update.message.text

        if not text:

            logger.debug("Нет текста в сообщении")

            return

        logger.info(f"Получено сообщение в группе {update.message.chat.id}: {text}")

        bot_username = context.bot.username if context.bot and context.bot.username else None

        if bot_username:

            bot_username_lower = bot_username.lower()

            if f"@{bot_username_lower}" not in text.lower():

                logger.debug("Бот не упомянут — пропускаем сообщение")

                return

        else:

            logger.error("Не удалось получить username бота")

            return

        if text.startswith('/'):

            return

        amount, description = parse_expense(text, bot_username)

        if amount is None:

            logger.debug(f"Не удалось распарсить расход из: {text}")

            return

        logger.info(f"Распарсен расход: {amount} - {description}")

        current_time = get_bishkek_now()

        today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

        today_end = current_time.replace(hour=23, minute=59, second=59, microsecond=999999)

        suggestions = find_similar_today_descriptions(

            chat_id=update.message.chat.id,

            target_description=description,

            start_date=today_start,

            end_date=today_end,

        )

        if suggestions:

            context.user_data["pending_expense_add"] = {

                "chat_id": update.message.chat.id,

                "user_id": update.message.from_user.id,

                "username": update.message.from_user.username or update.message.from_user.first_name or "Неизвестный",

                "amount": amount,

                "description": description,

                "suggestions": suggestions,

                "message_id": update.message.message_id,

                "original_message_id": update.message.reply_to_message.message_id if update.message.reply_to_message else None,

            }

            keyboard = _expense_name_suggestions_keyboard(description, suggestions)

            await update.message.reply_text(

                "🔍 Нашёл похожие расходы за сегодня.\n"

                "Выберите правильное название, чтобы расходы склеились в один пункт дня:",

                reply_to_message_id=update.message.message_id,

                reply_markup=keyboard,

            )

            return

        await finalize_expense_add(update, context, amount=amount, description=description)

    except Exception as e:

        logger.error(f"Ошибка в handle_message: {e}", exc_info=True)

async def show_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE,

                       start_date: datetime = None, end_date: datetime = None):

    chat_id = update.message.chat.id

    expenses = db.get_expenses(chat_id, start_date, end_date)

    total = db.get_total_amount(chat_id, start_date, end_date)

    is_single_day_period = (

        start_date is not None

        and end_date is not None

        and start_date.date() == end_date.date()

    )

    if not expenses:

        period_text = ""

        if start_date and end_date:

            period_text = f" за период {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"

        elif start_date:

            period_text = f" с {start_date.strftime('%d.%m.%Y')}"

        await update.message.reply_text(f"📭 Нет расходов{period_text}")

        await _delete_command_message(update)

        return

    message_parts = []

    if start_date and end_date:

        period_text = f"📅 Период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}\n"

    elif start_date:

        period_text = f"📅 С {start_date.strftime('%d.%m.%Y')}\n"

    else:

        period_text = ""

    if is_single_day_period:

        grouped = {}

        for expense in expenses:

            expense_id, _, _, amount, description, _, _, _ = expense

            key = normalize_description(description)

            if key not in grouped:

                grouped[key] = {

                    "description": (description or "Без описания").strip() or "Без описания",

                    "amount": 0.0,

                    "count": 0,

                    "expense_ids": [],

                }

            grouped[key]["amount"] += float(amount or 0)

            grouped[key]["count"] += 1

            grouped[key]["expense_ids"].append(int(expense_id))

        grouped_items = sorted(grouped.values(), key=lambda item: item["amount"], reverse=True)

        header = (

            f"{period_text}💰 Позиций расходов: {len(grouped_items)}\n"

            f"🧾 Всего записей: {len(expenses)}\n"

            f"💵 Общая сумма: {total:.2f} сом\n\n"

        )

        message_parts.append(escape(header))

        for item in grouped_items:

            suffix = f" ({item['count']} шт.)" if item["count"] > 1 else ""

            ids_text = ", ".join(str(exp_id) for exp_id in item["expense_ids"])

            message_parts.append(

                f"💸 {item['amount']:.2f} сом - {escape(item['description'])}{suffix}\n"

                f"🆔 ID: {ids_text}\n"

            )

        full_message = "\n".join(message_parts)

        token = uuid.uuid4().hex[:10]

        context.chat_data.setdefault("grouped_expenses_views", {})[token] = {

            "groups": grouped_items,

            "created_at": int(time.time()),

        }

        keyboard = _grouped_expenses_keyboard(token, grouped_items)

        await update.message.reply_text(full_message, parse_mode='HTML', reply_markup=keyboard)

        await _delete_command_message(update)

        return

    header = f"{period_text}💰 Всего расходов: {len(expenses)}\n💵 Общая сумма: {total:.2f} сом\n\n"

    message_parts.append(escape(header))

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

            f"👤 {escape(username)} | {date_obj.strftime('%d.%m.%Y %H:%M')} | {link_text}\n"

        )

        message_parts.append(expense_text)

    full_message = "\n".join(message_parts)

    if len(full_message) > 4096:

        await update.message.reply_text(

            escape(f"{period_text}💰 Всего расходов: {len(expenses)}\n💵 Общая сумма: {total:.2f} сом"),

            parse_mode='HTML'

        )

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

        await _delete_command_message(update)

    else:

        await update.message.reply_text(full_message, parse_mode='HTML')

        await _delete_command_message(update)

async def _delete_command_message(update: Update) -> None:

    if not update.message:

        return

    try:

        await update.message.delete()

    except Exception:

        pass

def get_bishkek_now():

    return datetime.now(BISHKEK_TZ)

def get_bishkek_today():

    now = get_bishkek_now()

    return now.replace(hour=0, minute=0, second=0, microsecond=0)

def check_network_connectivity():

    try:

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        sock.settimeout(5)

        result = sock.connect_ex(('api.telegram.org', 443))

        sock.close()

        return result == 0

    except Exception as e:

        logger.warning(f"Ошибка при проверке подключения: {e}")

        return False

def create_request_with_proxy():

    if not PROXY_URL:

        return None

    try:

        proxy_url = PROXY_URL

        if PROXY_USERNAME and PROXY_PASSWORD:

            if '://' in proxy_url:

                scheme, rest = proxy_url.split('://', 1)

                if '@' not in rest:

                    proxy_url = f"{scheme}://{PROXY_USERNAME}:{PROXY_PASSWORD}@{rest}"

        logger.info(f"Используется прокси: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

        return HTTPXRequest(proxy=proxy_url)

    except Exception as e:

        logger.error(f"Ошибка при настройке прокси: {e}")

        print(f"⚠️  Предупреждение: Не удалось настроить прокси: {e}")

        return None

async def expenses_today(update: Update, context: ContextTypes.DEFAULT_TYPE):

    today_start = get_bishkek_today()

    today_end = today_start.replace(hour=23, minute=59, second=59, microsecond=999999)

    await show_expenses(update, context, start_date=today_start, end_date=today_end)

async def expenses_week(update: Update, context: ContextTypes.DEFAULT_TYPE):

    today = get_bishkek_today()

    week_ago = today - timedelta(days=7)

    end = today.replace(hour=23, minute=59, second=59, microsecond=999999)

    await show_expenses(update, context, start_date=week_ago, end_date=end)

async def expenses_month(update: Update, context: ContextTypes.DEFAULT_TYPE):

    today = get_bishkek_today()

    month_ago = today - timedelta(days=30)

    end = today.replace(hour=23, minute=59, second=59, microsecond=999999)

    await show_expenses(update, context, start_date=month_ago, end_date=end)

async def expenses_period(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args or len(context.args) < 2:

        await update.message.reply_text(

            "📅 Укажите период в формате:\n"

            "/expenses_period 01.01.2024 31.01.2024\n"

            "или\n"

            "/expenses_period 2024-01-01 2024-01-31"

        )

        await _delete_command_message(update)

        return

    try:

        start_date = date_parser.parse(context.args[0], dayfirst=True)

        end_date = date_parser.parse(context.args[1], dayfirst=True)

        if start_date.tzinfo is None:

            start_date = start_date.replace(tzinfo=BISHKEK_TZ)

        if end_date.tzinfo is None:

            end_date = end_date.replace(tzinfo=BISHKEK_TZ)

        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

        await show_expenses(update, context, start_date=start_date, end_date=end_date)

    except Exception as e:

        await update.message.reply_text(f"❌ Ошибка парсинга даты: {e}")

        await _delete_command_message(update)

def _expense_actions_keyboard(expense_id: int, user_msg_id: int = None) -> InlineKeyboardMarkup:

    suffix = f":{user_msg_id}" if user_msg_id is not None else ""

    return InlineKeyboardMarkup([

        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"exp_confirm:{expense_id}{suffix}")],

        [

            InlineKeyboardButton("💵 Изменить сумму", callback_data=f"exp_amt:{expense_id}{suffix}"),

            InlineKeyboardButton("📝 Изменить название", callback_data=f"exp_name:{expense_id}{suffix}"),

        ],

        [InlineKeyboardButton("🗑 Удалить расход", callback_data=f"exp_del:{expense_id}{suffix}")],

    ])

def _grouped_expenses_keyboard(token: str, grouped_items: list[dict]) -> Optional[InlineKeyboardMarkup]:

    rows = []

    for idx, item in enumerate(grouped_items):

        if item["count"] < 2:

            continue

        desc = item["description"]

        short_desc = (desc[:18] + "...") if len(desc) > 19 else desc

        rows.append([

            InlineKeyboardButton(

                f"📂 {short_desc} ({item['count']})",

                callback_data=f"grp_day:{token}:{idx}",

            )

        ])

    return InlineKeyboardMarkup(rows) if rows else None

def _grouped_expense_ids_keyboard(expense_ids: list[int]) -> InlineKeyboardMarkup:

    rows = []

    for expense_id in expense_ids:

        rows.append([InlineKeyboardButton(f"🧾 Открыть ID {expense_id}", callback_data=f"grp_open:{expense_id}")])

    return InlineKeyboardMarkup(rows)

def _parse_expense_callback_data(data: str):

    if not data or not data.startswith("exp_"):

        return None, None, None

    parts = data.split(":")

    if len(parts) < 2:

        return None, None, None

    action, expense_id_str = parts[0], parts[1]

    user_msg_id = int(parts[2]) if len(parts) > 2 else None

    try:

        expense_id = int(expense_id_str)

    except ValueError:

        return None, None, None

    return action, expense_id, user_msg_id

async def handle_grouped_expenses_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if not query:

        return

    await query.answer()

    data = query.data or ""

    if data.startswith("grp_day:"):

        parts = data.split(":")

        if len(parts) != 3:

            await query.answer("❌ Неверный формат кнопки", show_alert=True)

            return

        token = parts[1]

        try:

            idx = int(parts[2])

        except ValueError:

            await query.answer("❌ Неверный индекс группы", show_alert=True)

            return

        groups_map = context.chat_data.get("grouped_expenses_views", {})

        payload = groups_map.get(token)

        if not payload:

            await query.answer("⌛ Список устарел. Запросите /expenses заново.", show_alert=True)

            return

        groups = payload.get("groups", [])

        if idx < 0 or idx >= len(groups):

            await query.answer("❌ Группа не найдена", show_alert=True)

            return

        group = groups[idx]

        expense_ids = group.get("expense_ids", [])

        if not expense_ids:

            await query.answer("❌ Нет расходов в группе", show_alert=True)

            return

        lines = [

            f"📂 {escape(group.get('description', 'Без описания'))}",

            f"🧾 Записей: {len(expense_ids)}",

            f"💵 Итого: {float(group.get('amount', 0)):.2f} сом",

            "",

            "Выберите расход:",

        ]

        for exp_id in expense_ids:

            expense = db.get_expense_by_id(exp_id, query.message.chat.id)

            if not expense:

                continue

            _, _, username, amount, description, _, _, date_str = expense

            date_obj = parse_db_datetime(date_str)

            lines.append(

                f"• ID {exp_id} — {float(amount):.2f} сом | {escape(username)} | {date_obj.strftime('%H:%M')}"

            )

        await query.edit_message_text(

            "\n".join(lines),

            parse_mode="HTML",

            reply_markup=_grouped_expense_ids_keyboard(expense_ids),

        )

        return

    if data.startswith("grp_open:"):

        parts = data.split(":")

        if len(parts) != 2:

            await query.answer("❌ Неверная кнопка", show_alert=True)

            return

        try:

            expense_id = int(parts[1])

        except ValueError:

            await query.answer("❌ Неверный ID", show_alert=True)

            return

        expense = db.get_expense_by_id(expense_id, query.message.chat.id)

        if not expense:

            await query.answer("❌ Расход не найден", show_alert=True)

            return

        _, _, username, amount, description, _, message_id, date_str = expense

        date_obj = parse_db_datetime(date_str)

        if message_id:

            chat_username = query.message.chat.username

            message_link = build_message_link(query.message.chat.id, chat_username, message_id)

            link_text = f'<a href="{message_link}">#{message_id}</a>'

        else:

            link_text = "-"

        await query.edit_message_text(

            f"🧾 Расход (ID: {expense_id})\n"

            f"💸 {float(amount):.2f} сом — {escape(description)}\n"

            f"👤 {escape(username)} | {date_obj.strftime('%d.%m.%Y %H:%M')} | {link_text}",

            parse_mode="HTML",

            reply_markup=_expense_actions_keyboard(expense_id),

        )

        return

async def _delete_messages_safe(context, chat_id: int, *message_ids: int):

    for mid in message_ids:

        if mid is None:

            continue

        try:

            await context.bot.delete_message(chat_id=chat_id, message_id=mid)

        except Exception:

            pass

async def handle_expense_suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if not query:

        return

    await query.answer()

    pending = context.user_data.get("pending_expense_add")

    if not pending:

        await query.edit_message_text("⌛ Подсказка устарела. Отправьте расход заново.")

        return

    if query.message.chat.id != pending.get("chat_id") or query.from_user.id != pending.get("user_id"):

        await query.answer("❌ Это не ваша подсказка", show_alert=True)

        return

    callback_data = query.data or ""

    final_description = pending.get("description", "")

    if callback_data == "exp_suggest_cancel":

        context.user_data.pop("pending_expense_add", None)

        await query.edit_message_text("❌ Добавление расхода отменено.")

        return

    if callback_data.startswith("exp_suggest_use:"):

        try:

            idx = int(callback_data.split(":", 1)[1])

            suggestions = pending.get("suggestions") or []

            if idx < 0 or idx >= len(suggestions):

                raise ValueError("invalid suggestion index")

            final_description = suggestions[idx]

        except Exception:

            await query.answer("❌ Не удалось выбрать подсказку", show_alert=True)

            return

    elif callback_data != "exp_suggest_keep":

        await query.answer("❌ Неизвестное действие", show_alert=True)

        return

    try:

        await query.edit_message_text(

            f"✅ Выбрано название: {escape(final_description)}",

            parse_mode="HTML"

        )

    except Exception:

        pass

    try:

        chat_id = int(pending.get("chat_id"))

        user_id = int(pending.get("user_id"))

        username = str(pending.get("username") or "Неизвестный")

        amount = float(pending.get("amount", 0))

        user_msg_id = int(pending.get("message_id"))

        original_message_id = pending.get("original_message_id")

        if original_message_id is not None:

            original_message_id = int(original_message_id)

        current_time = get_bishkek_now()

        daily_limit = get_chat_daily_limit(chat_id)

        today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

        today_end = current_time.replace(hour=23, minute=59, second=59, microsecond=999999)

        previous_total_today = db.get_total_amount(

            chat_id=chat_id,

            start_date=today_start,

            end_date=today_end

        )

        expense_id = db.add_expense(

            chat_id=chat_id,

            user_id=user_id,

            username=username,

            amount=amount,

            description=final_description,

            message_id=user_msg_id,

            expense_date=current_time

        )

        new_total_today = (previous_total_today or 0) + amount

        record = {

            "id": expense_id,

            "chat_id": chat_id,

            "user_id": user_id,

            "username": username,

            "amount": amount,

            "description": final_description,

            "message_id": user_msg_id,

            "original_message_id": original_message_id,

            "created_at": current_time.isoformat(),

        }

        try:

            with open(EXPENSES_JSONL_PATH, "a", encoding="utf-8") as f:

                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        except Exception as log_err:

            logger.error(f"Не удалось записать расход в JSONL: {log_err}", exc_info=True)

        reply_text = (

            f"✅ Расход добавлен (ID: {expense_id})\n"

            f"💸 {amount:.2f} сом — {escape(final_description)}\n"

            f"📎 Ответ на сообщение #{original_message_id if original_message_id else '-'}"

        )

        confirm_keyboard = _expense_actions_keyboard(expense_id, user_msg_id)

        await context.bot.send_message(

            chat_id=chat_id,

            text=reply_text,

            reply_to_message_id=user_msg_id,

            reply_markup=confirm_keyboard,

            parse_mode="HTML"

        )

        if new_total_today > daily_limit:

            warning_text = (

                "🚨 ПРЕВЫШЕН ДНЕВНОЙ ЛИМИТ ПО РАСХОДАМ!\n"

                f"💵 Общая сумма за сегодня: {new_total_today:.2f} сом\n"

                f"📊 Установленный лимит: {daily_limit:.2f} сом"

            )

            await context.bot.send_message(chat_id=chat_id, text=warning_text)

    except Exception as e:

        logger.error(f"Ошибка при сохранении расхода через подсказку: {e}", exc_info=True)

        await context.bot.send_message(

            chat_id=query.message.chat.id,

            text=f"❌ Ошибка при сохранении расхода: {e}",

            reply_to_message_id=query.message.message_id if query.message else None

        )

    finally:

        context.user_data.pop("pending_expense_add", None)

async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    action, expense_id, user_msg_id = _parse_expense_callback_data(query.data or "")

    if expense_id is None:

        return

    chat_id = query.message.chat.id

    bot_msg_id = query.message.message_id

    user_id = query.from_user.id if query.from_user else None

    if action == "exp_confirm":

        expense = db.get_expense_by_id(expense_id, chat_id)

        if not expense:

            await query.edit_message_text("❌ Расход не найден.", parse_mode="HTML")

            return

        _, _, username, amount, description, _, _, date_str = expense

        await query.edit_message_text(

            f"✅ Расход (ID: {expense_id})\n"

            f"💸 {amount:.2f} сом — {escape(description)}\n"

            f"👤 {escape(username)}\n\n"

            "✅ Подтверждено",

            parse_mode="HTML",

            reply_markup=None,

        )

        return

    if action in ("exp_amt", "exp_name"):

        expense = db.get_expense_by_id(expense_id, chat_id)

        if not expense:

            await query.edit_message_text("❌ Расход не найден.", parse_mode="HTML")

            return

        field = "amount" if action == "exp_amt" else "description"

        prompt_text = "✏️ Напишите новую сумму (одним числом):" if field == "amount" else "✏️ Напишите новое название расхода:"

        await context.bot.send_message(chat_id, prompt_text)

        context.user_data["exp_edit"] = {

            "expense_id": expense_id,

            "chat_id": chat_id,

            "bot_msg_id": bot_msg_id,

            "user_msg_id": user_msg_id,

            "field": field,

            "user_id": user_id,

        }

        return

    if action == "exp_del":

        expense = db.get_expense_by_id(expense_id, chat_id)

        if not expense:

            await query.edit_message_text("❌ Расход не найден.", parse_mode="HTML")

            return

        expense_owner_id = expense[1]

        can_delete = (

            get_user_role_from_db(chat_id, user_id) in ["администратор", "шеф"]

            or expense_owner_id == user_id

        )

        if not can_delete:

            await query.answer("❌ Нельзя удалить чужой расход.", show_alert=True)

            return

        deleted = db.delete_expense(

            expense_id, chat_id, user_id if expense_owner_id == user_id else None, force=(expense_owner_id != user_id)

        )

        if deleted:

            amount, desc = expense[3], expense[4]

            await query.edit_message_text(

                f"🗑 Расход удалён\n💸 {amount:.2f} сом — {escape(desc)}",

                parse_mode="HTML",

                reply_markup=None,

            )

        else:

            await query.answer("❌ Не удалось удалить.", show_alert=True)

async def handle_expense_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:

        return

    exp_edit = context.user_data.get("exp_edit")

    if not exp_edit:

        return

    if exp_edit.get("chat_id") != update.message.chat.id or exp_edit.get("user_id") != update.message.from_user.id:

        return

    expense_id = exp_edit["expense_id"]

    chat_id = exp_edit["chat_id"]

    bot_msg_id = exp_edit.get("bot_msg_id")

    user_msg_id = exp_edit.get("user_msg_id")

    field = exp_edit["field"]

    text = update.message.text.strip()

    del context.user_data["exp_edit"]

    expense = db.get_expense_by_id(expense_id, chat_id)

    if not expense:

        await update.message.reply_text("❌ Расход не найден.", parse_mode="HTML")

        context.user_data["_last_edit_message_id"] = update.message.message_id

        return

    _, _, username, amount, description, _, _, date_str = expense

    if field == "amount":

        amount_match = re.search(r"(\d+(?:[.,]\d+)?)", text)

        if not amount_match:

            await update.message.reply_text("❌ Не удалось распознать число. Напишите сумму, например: 1500")

            return

        try:

            new_amount = float(amount_match.group(1).replace(",", "."))

        except ValueError:

            await update.message.reply_text("❌ Неверный формат суммы.")

            return

        if not db.update_expense_amount(expense_id, chat_id, new_amount):

            await update.message.reply_text("❌ Не удалось обновить сумму.", parse_mode="HTML")

            return

        amount = new_amount

    else:

        if not text or len(text) > 500:

            await update.message.reply_text("❌ Название не может быть пустым или длиннее 500 символов.")

            return

        if not db.update_expense_description(expense_id, chat_id, text):

            await update.message.reply_text("❌ Не удалось обновить название.", parse_mode="HTML")

            return

        description = text

    reply_text = (

        f"✅ Расход добавлен (ID: {expense_id})\n"

        f"💸 {amount:.2f} сом — {escape(description)}\n"

        f"📎 Ответ на сообщение #{user_msg_id or '-'}"

    )

    keyboard = _expense_actions_keyboard(expense_id, user_msg_id)

    try:

        await context.bot.edit_message_text(

            chat_id=chat_id,

            message_id=bot_msg_id,

            text=reply_text,

            reply_markup=keyboard,

            parse_mode="HTML",

        )

        await update.message.reply_text("✅ Изменено. Сообщение выше обновлено.")

    except Exception as e:

        logger.debug(f"Не удалось отредактировать сообщение расхода: {e}")

        await update.message.reply_text(f"✅ Изменено. Расход ID {expense_id} — {amount:.2f} сом — {escape(description)}", parse_mode="HTML")

    if field == "amount":

        try:

            current_time = get_bishkek_now()

            today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

            today_end = current_time.replace(hour=23, minute=59, second=59, microsecond=999999)

            total_today = db.get_total_amount(

                chat_id=chat_id,

                start_date=today_start,

                end_date=today_end

            ) or 0

            daily_limit = get_chat_daily_limit(chat_id)

            if total_today > daily_limit:

                warning_text = (

                    "🚨 ПРЕВЫШЕН ДНЕВНОЙ ЛИМИТ ПО РАСХОДАМ!\n"

                    f"💵 Общая сумма за сегодня: {total_today:.2f} сом\n"

                    f"📊 Установленный лимит: {daily_limit:.2f} сом"

                )

                await context.bot.send_message(

                    chat_id=chat_id,

                    text=warning_text

                )

        except Exception as warn_err:

            logger.error(f"Ошибка при отправке предупреждения о превышении лимита при редактировании: {warn_err}", exc_info=True)

    context.user_data["_last_edit_message_id"] = update.message.message_id

async def delete_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args or len(context.args) < 1:

        await update.message.reply_text(

            "❌ Укажите ID расхода для удаления\n"

            "Пример: /delete 5\n\n"

            "ID можно найти в списке расходов (команда /expenses)"

        )

        await _delete_command_message(update)

        return

    try:

        expense_id = int(context.args[0])

        chat_id = update.message.chat.id

        user_id = update.message.from_user.id

        expense = db.get_expense_by_id(expense_id, chat_id)

        if not expense:

            await update.message.reply_text(f"❌ Расход с ID {expense_id} не найден")

            await _delete_command_message(update)

            return

        expense_owner_id = expense[1]

        user_role = get_user_role_from_db(chat_id, user_id)

        can_delete_any = user_role in ["администратор", "шеф"]

        if not can_delete_any and expense_owner_id != user_id:

            await update.message.reply_text(

                f"❌ Вы можете удалять только свои расходы.\n"

                f"Только администраторы и шефы могут удалять чужие расходы."

            )

            await _delete_command_message(update)

            return

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

            await _delete_command_message(update)

        else:

            await update.message.reply_text(

                f"❌ Не удалось удалить расход.\n"

                f"Возможно, расход уже был удален или произошла ошибка."

            )

            await _delete_command_message(update)

    except ValueError:

        await update.message.reply_text("❌ ID должен быть числом")

        await _delete_command_message(update)

    except Exception as e:

        logger.error(f"Ошибка при удалении расхода: {e}", exc_info=True)

        await update.message.reply_text(f"❌ Ошибка при удалении: {e}")

        await _delete_command_message(update)

async def export_today_pm(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:

        return

    chat = update.message.chat

    chat_id = chat.id

    chat_type = chat.type

    issuer = update.message.from_user

    issuer_id = issuer.id

    if chat_type not in ["group", "supergroup"]:

        await update.message.reply_text("❌ Эту команду нужно вызывать в группе, где ведётся учёт расходов.")

        await _delete_command_message(update)

        return

    issuer_role = get_user_role_from_db(chat_id, issuer_id)

    is_privileged = issuer_role in ["администратор", "шеф"]

    if not is_privileged:

        try:

            member = await context.bot.get_chat_member(chat_id, issuer_id)

            if member.status not in ["administrator", "creator"]:

                await update.message.reply_text("❌ Только администраторы и шефы могут делать выгрузку расходов")

                await _delete_command_message(update)

                return

        except Exception:

            await update.message.reply_text("❌ Только администраторы и шефы могут делать выгрузку расходов")

            await _delete_command_message(update)

            return

    target_username = None

    if context.args:

        arg = context.args[0]

        if arg.startswith("@"):

            target_username = arg.lstrip("@").strip()

        else:

            target_username = arg.strip()

    base_today = get_bishkek_today()

    command = update.message.text.split()[0] if update.message.text else "/export_today_pm"

    if command == "/export_week_pm":

        end_date = base_today.replace(hour=23, minute=59, second=59, microsecond=999999)

        start_date = base_today - timedelta(days=7)

        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    elif command == "/export_month_pm":

        end_date = base_today.replace(hour=23, minute=59, second=59, microsecond=999999)

        start_date = base_today - timedelta(days=30)

        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    else:

        start_date = base_today

        end_date = base_today.replace(hour=23, minute=59, second=59, microsecond=999999)

    expenses = db.get_expenses(chat_id, start_date=start_date, end_date=end_date)

    if target_username:

        filtered = []

        for exp in expenses:

            _, _, username, amount, description, category, message_id, date_str = exp

            if username and username.lower() == target_username.lower():

                filtered.append(exp)

        expenses = filtered

    if not expenses:

        if target_username:

            await update.message.reply_text(f"📭 Нет расходов за выбранный период для пользователя @{target_username}.")

        else:

            await update.message.reply_text("📭 Нет расходов за выбранный период.")

        await _delete_command_message(update)

        return

    lines = []

    for exp in expenses:

        expense_id, user_id, username, amount, description, category, message_id, date_str = exp

        date_obj = parse_db_datetime(date_str)

        username_display = username or "нет username"

        line = (

            f"{date_obj.strftime('%Y-%m-%d %H:%M')} | "

            f"{amount:.2f} | "

            f"{description} | "

            f"{username_display} | "

            f"ID:{expense_id}"

        )

        lines.append(line)

    txt_content = "\n".join(lines)

    period_label = f"{start_date.strftime('%Y-%m-%d')}..{end_date.strftime('%Y-%m-%d')}"

    filename = f"expenses_{chat_id}_{period_label}.txt"

    if target_username:

        filename = f"expenses_{chat_id}_{target_username}_{period_label}.txt"

    file_bytes = io.BytesIO(txt_content.encode("utf-8"))

    file_bytes.name = filename

    caption = f"💾 Выгрузка расходов за период {period_label}\nЧат: {chat.title or chat_id}\nКоличество записей: {len(lines)}"

    if target_username:

        caption += f"\nФильтр по пользователю: @{target_username}"

    try:

        await context.bot.send_document(

            chat_id=issuer_id,

            document=file_bytes,

            filename=filename,

            caption=caption,

        )

        await update.message.reply_text("✅ Выгрузка отправлена вам в личные сообщения.")

        await _delete_command_message(update)

    except Exception as e:

        logger.error(f"Ошибка при отправке выгрузки в личку: {e}", exc_info=True)

        await update.message.reply_text(

            "❌ Не удалось отправить файл в личку.\n"

            "Убедитесь, что вы писали боту в личные сообщения (нажмите /start в личке с ботом)."

        )

        await _delete_command_message(update)

async def set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:

        return

    chat = update.message.chat

    chat_id = chat.id

    user = update.message.from_user

    user_id = user.id

    issuer_role = get_user_role_from_db(chat_id, user_id)

    is_privileged = issuer_role in ["администратор", "шеф"]

    if not is_privileged:

        try:

            member = await context.bot.get_chat_member(chat_id, user_id)

            if member.status not in ["administrator", "creator"]:

                await update.message.reply_text("❌ Только администраторы и шефы могут менять лимит")

                return

        except Exception:

            await update.message.reply_text("❌ Только администраторы и шефы могут менять лимит")

            return

    if not context.args:

        current_limit = get_chat_daily_limit(chat_id)

        await update.message.reply_text(

            f"📊 Текущий суточный лимит: {current_limit:.2f} сом\n"

            f"(Лимит по умолчанию: {DAILY_EXPENSE_LIMIT:.2f} сом)"

        )

        await _delete_command_message(update)

        return

    arg = context.args[0].strip().lower()

    if arg in ["off", "none", "0"]:

        try:

            db.set_daily_limit(chat_id, None)

            await update.message.reply_text(

                f"✅ Лимит сброшен. Теперь используется лимит по умолчанию: {DAILY_EXPENSE_LIMIT:.2f} сом"

            )

            await _delete_command_message(update)

        except Exception as e:

            logger.error(f"Ошибка при сбросе дневного лимита: {e}", exc_info=True)

            await update.message.reply_text(f"❌ Ошибка при сбросе лимита: {e}")

            await _delete_command_message(update)

        return

    try:

        raw_value = context.args[0].replace(" ", "").replace(",", ".")

        limit_value = float(raw_value)

        if limit_value <= 0:

            await update.message.reply_text("❌ Лимит должен быть положительным числом")

            await _delete_command_message(update)

            return

    except ValueError:

        await update.message.reply_text(

            "❌ Не удалось распознать число.\n"

            "Примеры:\n"

            "/limit 600000\n"

            "/limit 250000.50\n"

            "/limit off  — сбросить лимит к значению по умолчанию"

        )

        await _delete_command_message(update)

        return

    try:

        db.set_daily_limit(chat_id, limit_value)

        await update.message.reply_text(

            f"✅ Лимит обновлён: {limit_value:.2f} сом"

        )

        await _delete_command_message(update)

    except Exception as e:

        logger.error(f"Ошибка при установке дневного лимита: {e}", exc_info=True)

        await update.message.reply_text(f"❌ Ошибка при установке лимита: {e}")

        await _delete_command_message(update)

def main():

    if not BOT_TOKEN:

        print("❌ Ошибка: BOT_TOKEN не установлен!")

        print("Установите переменную окружения BOT_TOKEN или добавьте токен в код")

        return

    request = create_request_with_proxy()

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

    global_application = None

    def signal_handler(signum, frame):

        logger.info(f"Получен сигнал {signum}, останавливаем бота...")

        if global_application:

            try:

                global_application.stop()

            except:

                pass

        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    if hasattr(signal, 'SIGTERM'):

        signal.signal(signal.SIGTERM, signal_handler)

    def cleanup():

        logger.info("Выполняется очистка перед выходом...")

        if global_application:

            try:

                global_application.stop()

            except:

                pass

    atexit.register(cleanup)

    try:

        builder = Application.builder().token(BOT_TOKEN)

        if request:

            builder = builder.request(request)

        application = builder.build()

        global_application = application

    except Exception as e:

        print(f"\n❌ Ошибка при создании приложения: {e}")

        print("Проверьте правильность токена бота и настройки прокси (если используется).")

        sys.exit(1)

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

    application.add_handler(CommandHandler("limit", set_limit))

    application.add_handler(CommandHandler("export_today_pm", export_today_pm))

    application.add_handler(CommandHandler("export_week_pm", export_today_pm))

    application.add_handler(CommandHandler("export_month_pm", export_today_pm))

    application.add_handler(CallbackQueryHandler(handle_expense_suggestion_callback, pattern="^exp_suggest"))

    application.add_handler(CallbackQueryHandler(handle_grouped_expenses_callback, pattern="^grp_"))

    application.add_handler(CallbackQueryHandler(handle_expense_callback, pattern="^exp_"))

    message_filter = filters.ChatType.GROUPS & (filters.TEXT | filters.REPLY)

    application.add_handler(MessageHandler(message_filter, handle_message))

    async def debug_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

        if update.message and update.message.chat.type in ['group', 'supergroup']:

            logger.info(f"DEBUG: Получено сообщение в группе {update.message.chat.id}, тип: {update.message.chat.type}, текст: {update.message.text}")

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:

        logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

        if isinstance(update, Update) and update.message:

            try:

                await update.message.reply_text(f"❌ Произошла ошибка: {context.error}")

            except:

                pass

    application.add_error_handler(error_handler)

    async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

        if update.message:

            logger.info(f"Получено сообщение: chat_id={update.message.chat.id}, user_id={update.message.from_user.id}, text={update.message.text}")

        elif update.callback_query:

            logger.info(f"Получен callback_query: chat_id={update.callback_query.message.chat.id if update.callback_query.message else None}")

    application.add_handler(MessageHandler(filters.ALL, log_update), group=-1)

    logger.info("Бот запущен...")

    try:

        print("Бот запущен...")

        print("Бот будет обрабатывать сообщения с расходами в группах")

        print("Примеры: '100 еда', '500 на такси', '1500 - продукты'")

    except UnicodeEncodeError:

        print("Bot started...")

        print("Bot will process expense messages in groups")

        print("Examples: '100 food', '500 taxi', '1500 - products'")

    max_retries = 5

    retry_count = 0

    while retry_count < max_retries:

        try:

            application.run_polling(

                allowed_updates=Update.ALL_TYPES,

                drop_pending_updates=True

            )

            break

        except Conflict as e:

            print("\n❌ ОШИБКА: Уже запущен другой экземпляр бота!")

            print("Telegram не позволяет запускать несколько экземпляров бота одновременно.")

            print("\nРешение:")

            print("1. Остановите все запущенные экземпляры бота")

            print("2. Подождите 10-20 секунд")

            print("3. Запустите бота снова")

            print("\nДля остановки всех процессов Python с ботом:")

            print("  Get-Process python | Stop-Process")

            logger.error(f"Конфликт: другой экземпляр бота уже запущен: {e}")

            sys.exit(1)

        except (NetworkError, TimedOut) as e:

            retry_count += 1

            error_msg = str(e)

            logger.error(f"Сетевая ошибка (попытка {retry_count}/{max_retries}): {error_msg}")

            if retry_count < max_retries:

                wait_time = retry_count * 5

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
