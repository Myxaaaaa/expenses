import os
from datetime import datetime
from typing import List, Tuple, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dateutil import tz


class PostgresDatabase:
    """
    Реализация тех же методов, что и в Database (SQLite),
    но поверх PostgreSQL. Использует переменную окружения DATABASE_URL.
    """

    def __init__(self) -> None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not set for PostgresDatabase")
        self.dsn = dsn
        self.init_db()

    def get_connection(self):
        return psycopg2.connect(self.dsn, cursor_factory=RealDictCursor)

    def init_db(self) -> None:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username TEXT,
                amount DOUBLE PRECISION NOT NULL,
                description TEXT NOT NULL,
                category TEXT,
                message_id BIGINT,
                date TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username TEXT,
                role TEXT NOT NULL,
                assigned_by BIGINT,
                assigned_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT roles_chat_user_unique UNIQUE (chat_id, user_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS names (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username TEXT,
                name TEXT NOT NULL,
                assigned_by BIGINT,
                assigned_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT names_chat_user_unique UNIQUE (chat_id, user_id)
            );
            """
        )
        conn.commit()
        conn.close()

    def add_expense(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        amount: float,
        description: str,
        category: str = None,
        message_id: int = None,
        expense_date: datetime = None,
    ) -> int:
        conn = self.get_connection()
        cur = conn.cursor()

        bishkek_tz = tz.gettz("Asia/Bishkek")
        if expense_date is None:
            expense_date = datetime.now(bishkek_tz)

        if expense_date.tzinfo:
            expense_date_naive = (
                expense_date.astimezone(bishkek_tz).replace(tzinfo=None)
            )
        else:
            expense_date_naive = expense_date

        cur.execute(
            """
            INSERT INTO expenses (chat_id, user_id, username, amount, description, category, message_id, date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                chat_id,
                user_id,
                username,
                amount,
                description,
                category,
                message_id,
                expense_date_naive,
            ),
        )
        expense_id = cur.fetchone()["id"]
        conn.commit()
        conn.close()
        return expense_id

    def get_expenses(
        self,
        chat_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Tuple]:
        conn = self.get_connection()
        cur = conn.cursor()

        query = """
            SELECT id, user_id, username, amount, description, category, message_id, date
            FROM expenses
            WHERE chat_id = %s
        """
        params = [chat_id]

        bishkek_tz = tz.gettz("Asia/Bishkek")

        if start_date:
            query += " AND date >= %s"
            if start_date.tzinfo:
                start_local = start_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                start_local = start_date
            params.append(start_local)

        if end_date:
            query += " AND date <= %s"
            if end_date.tzinfo:
                end_local = end_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                end_local = end_date
            params.append(end_local)

        query += " ORDER BY date DESC"

        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        # Приводим к тому же формату кортежей, что и в SQLite-версии
        return [
            (
                r["id"],
                r["user_id"],
                r["username"],
                r["amount"],
                r["description"],
                r["category"],
                r["message_id"],
                r["date"],
            )
            for r in rows
        ]

    def get_total_amount(
        self,
        chat_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> float:
        conn = self.get_connection()
        cur = conn.cursor()

        query = "SELECT SUM(amount) AS total FROM expenses WHERE chat_id = %s"
        params = [chat_id]

        bishkek_tz = tz.gettz("Asia/Bishkek")

        if start_date:
            query += " AND date >= %s"
            if start_date.tzinfo:
                start_local = start_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                start_local = start_date
            params.append(start_local)

        if end_date:
            query += " AND date <= %s"
            if end_date.tzinfo:
                end_local = end_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                end_local = end_date
            params.append(end_local)

        cur.execute(query, params)
        row = cur.fetchone()
        conn.close()
        return float(row["total"] or 0.0)

    def set_role(
        self, chat_id: int, user_id: int, username: str, role: str, assigned_by: int
    ) -> None:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO roles (chat_id, user_id, username, role, assigned_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id)
            DO UPDATE SET
                role = EXCLUDED.role,
                username = EXCLUDED.username,
                assigned_by = EXCLUDED.assigned_by,
                assigned_at = CURRENT_TIMESTAMP;
            """,
            (chat_id, user_id, username, role, assigned_by),
        )
        conn.commit()
        conn.close()

    def get_roles(self, chat_id: int) -> List[Tuple]:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, username, role, assigned_at
            FROM roles
            WHERE chat_id = %s
            ORDER BY assigned_at DESC;
            """,
            (chat_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return [
            (r["user_id"], r["username"], r["role"], r["assigned_at"]) for r in rows
        ]

    def get_role(self, chat_id: int, user_id: int) -> Optional[Tuple]:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT role
            FROM roles
            WHERE chat_id = %s AND user_id = %s;
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return (row["role"],)

    def set_name(
        self, chat_id: int, user_id: int, username: str, name: str, assigned_by: int
    ) -> None:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO names (chat_id, user_id, username, name, assigned_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id)
            DO UPDATE SET
                name = EXCLUDED.name,
                username = EXCLUDED.username,
                assigned_by = EXCLUDED.assigned_by,
                assigned_at = CURRENT_TIMESTAMP;
            """,
            (chat_id, user_id, username, name, assigned_by),
        )
        conn.commit()
        conn.close()

    def get_name(self, chat_id: int, user_id: int) -> Optional[str]:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name
            FROM names
            WHERE chat_id = %s AND user_id = %s;
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()
        conn.close()
        return row["name"] if row else None

    def get_all_info(self, chat_id: int) -> List[Tuple]:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                r.user_id,
                r.username,
                r.role,
                n.name
            FROM roles r
            LEFT JOIN names n
                ON r.chat_id = n.chat_id AND r.user_id = n.user_id
            WHERE r.chat_id = %s
            UNION
            SELECT
                n.user_id,
                n.username,
                NULL as role,
                n.name
            FROM names n
            WHERE n.chat_id = %s AND n.user_id NOT IN (
                SELECT user_id FROM roles WHERE chat_id = %s
            )
            ORDER BY user_id;
            """,
            (chat_id, chat_id, chat_id),
        )
        rows = cur.fetchall()
        conn.close()
        return [
            (r["user_id"], r["username"], r["role"], r["name"]) for r in rows
        ]

    def get_user_id_by_username(self, chat_id: int, username: str) -> Optional[int]:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id
            FROM roles
            WHERE chat_id = %s AND LOWER(username) = LOWER(%s)
            LIMIT 1;
            """,
            (chat_id, username),
        )
        row = cur.fetchone()
        if row:
            conn.close()
            return row["user_id"]

        cur.execute(
            """
            SELECT user_id
            FROM names
            WHERE chat_id = %s AND LOWER(username) = LOWER(%s)
            LIMIT 1;
            """,
            (chat_id, username),
        )
        row = cur.fetchone()
        conn.close()
        return row["user_id"] if row else None

    def delete_expense(
        self, expense_id: int, chat_id: int, user_id: int = None, force: bool = False
    ) -> bool:
        conn = self.get_connection()
        cur = conn.cursor()

        if user_id is not None and not force:
            cur.execute(
                """
                DELETE FROM expenses
                WHERE id = %s AND chat_id = %s AND user_id = %s;
                """,
                (expense_id, chat_id, user_id),
            )
        else:
            cur.execute(
                """
                DELETE FROM expenses
                WHERE id = %s AND chat_id = %s;
                """,
                (expense_id, chat_id),
            )

        deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def get_expense_by_id(self, expense_id: int, chat_id: int) -> Optional[Tuple]:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, username, amount, description, category, message_id, date
            FROM expenses
            WHERE id = %s AND chat_id = %s;
            """,
            (expense_id, chat_id),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return (
            row["id"],
            row["user_id"],
            row["username"],
            row["amount"],
            row["description"],
            row["category"],
            row["message_id"],
            row["date"],
        )

