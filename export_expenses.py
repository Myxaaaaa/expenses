
import sqlite3

from datetime import datetime

from dateutil import tz

def parse_db_datetime(value):

    if isinstance(value, str):

        formats = [

            "%Y-%m-%d %H:%M:%S",

            "%Y-%m-%d %H:%M:%S.%f",

            "%Y-%m-%d",

        ]

        for fmt in formats:

            try:

                return datetime.strptime(value, fmt)

            except ValueError:

                continue

        return datetime.now()

    return value if isinstance(value, datetime) else datetime.now()

def export_all_expenses(db_name="expenses.db", output_file="all_expenses.txt"):

    conn = sqlite3.connect(db_name)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, chat_id, user_id, username, amount, description, category, message_id, date
        FROM expenses
        ORDER BY date DESC
    """)

    expenses = cursor.fetchall()

    cursor.execute("SELECT COUNT(*), SUM(amount) FROM expenses")

    total_count, total_amount = cursor.fetchone()

    total_amount = total_amount if total_amount else 0.0

    cursor.execute("SELECT DISTINCT chat_id FROM expenses ORDER BY chat_id")

    chat_ids = [row[0] for row in cursor.fetchall()]

    conn.close()

    lines = []

    lines.append("=" * 80)

    lines.append("ЭКСПОРТ ВСЕХ РАСХОДОВ")

    lines.append("=" * 80)

    lines.append(f"Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

    lines.append(f"Всего расходов: {total_count}")

    lines.append(f"Общая сумма: {total_amount:.2f} сом")

    lines.append(f"Количество чатов: {len(chat_ids)}")

    lines.append("=" * 80)

    lines.append("")

    for chat_id in chat_ids:

        chat_expenses = [e for e in expenses if e[1] == chat_id]

        chat_total = sum(e[4] for e in chat_expenses)

        lines.append("-" * 80)

        lines.append(f"ЧАТ ID: {chat_id}")

        lines.append(f"Расходов в чате: {len(chat_expenses)}")

        lines.append(f"Сумма в чате: {chat_total:.2f} сом")

        lines.append("-" * 80)

        lines.append("")

        for expense in chat_expenses:

            expense_id, chat_id_val, user_id, username, amount, description, category, message_id, date_str = expense

            date_obj = parse_db_datetime(date_str)

            date_formatted = date_obj.strftime('%d.%m.%Y %H:%M')

            expense_line = f"ID: {expense_id}"

            expense_line += f" | Сумма: {amount:.2f} сом"

            expense_line += f" | Описание: {description}"

            if category:

                expense_line += f" | Категория: {category}"

            expense_line += f" | Пользователь: {username} (ID: {user_id})"

            expense_line += f" | Дата: {date_formatted}"

            if message_id:

                expense_line += f" | Сообщение ID: {message_id}"

            lines.append(expense_line)

            lines.append("")

        lines.append("")

    with open(output_file, 'w', encoding='utf-8') as f:

        f.write('\n'.join(lines))

    print(f"✅ Экспорт завершен!")

    print(f"📄 Файл сохранен: {output_file}")

    print(f"📊 Всего расходов: {total_count}")

    print(f"💰 Общая сумма: {total_amount:.2f} сом")

    print(f"💬 Чатов: {len(chat_ids)}")

if __name__ == "__main__":

    export_all_expenses()
