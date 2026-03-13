import sqlite3
import os
from datetime import datetime
from typing import List, Tuple, Optional
from dateutil import tz


class Database:
    def __init__(self, db_name: str = "expenses.db"):
        """
        Инициализация базы данных.
        На Railway, если подключен Volume, БД будет храниться на Volume,
        чтобы не пропадать после перезапуска контейнера.
        """
        # 1. Явный путь из переменной окружения (можно задать самому)
        env_db_path = os.getenv("DB_PATH")

        # 2. Стандартный путь Railway Volume (если Volume подключен)
        railway_volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")

        if env_db_path:
            self.db_name = env_db_path
        elif railway_volume:
            # Кладём файл БД внутрь смонтированного тома
            self.db_name = os.path.join(railway_volume, "expenses.db")
        else:
            # Локальный запуск — обычный файл рядом с кодом
            self.db_name = db_name

        # Создаём папку при необходимости
        db_dir = os.path.dirname(self.db_name)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_name)
    
    def init_db(self):
        """Создает таблицы для хранения данных (расходы, роли, имена, настройки чата)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                amount REAL NOT NULL,
                description TEXT NOT NULL,
                category TEXT,
                message_id INTEGER,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                role TEXT NOT NULL,
                assigned_by INTEGER,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS names (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                name TEXT NOT NULL,
                assigned_by INTEGER,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, user_id)
            )
        """)
        # Таблица настроек чата (в том числе суточный лимит расходов)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                daily_limit REAL
            )
        """)
        conn.commit()
        conn.close()
    
    def add_expense(self, chat_id: int, user_id: int, username: str, 
                   amount: float, description: str, category: str = None, 
                   message_id: int = None, expense_date: datetime = None) -> int:
        """Добавляет новый расход"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Получаем часовой пояс Бишкека
        bishkek_tz = tz.gettz('Asia/Bishkek')
        
        # Если дата не указана, используем текущее время
        if expense_date is None:
            expense_date = datetime.now(bishkek_tz)
        
        # Сохраняем время в БД как строку в формате Бишкекского времени (без часового пояса)
        # Убираем часовой пояс для сохранения в БД
        if expense_date.tzinfo:
            expense_date_naive = expense_date.astimezone(bishkek_tz).replace(tzinfo=None)
        else:
            expense_date_naive = expense_date
        
        cursor.execute("""
            INSERT INTO expenses (chat_id, user_id, username, amount, description, category, message_id, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, user_id, username, amount, description, category, message_id, expense_date_naive))
        expense_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return expense_id
    
    def get_expenses(self, chat_id: int, start_date: Optional[datetime] = None, 
                    end_date: Optional[datetime] = None) -> List[Tuple]:
        """Получает все расходы за период"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT id, user_id, username, amount, description, category, message_id, date
            FROM expenses
            WHERE chat_id = ?
        """
        params = [chat_id]
        
        if start_date:
            query += " AND date >= ?"
            # Время в БД хранится как локальное время Бишкека (без часового пояса)
            # Конвертируем дату с часовым поясом в локальное время Бишкека
            if start_date.tzinfo:
                bishkek_tz = tz.gettz('Asia/Bishkek')
                start_date_local = start_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                start_date_local = start_date
            params.append(start_date_local.strftime("%Y-%m-%d %H:%M:%S"))

        if end_date:
            query += " AND date <= ?"
            # Время в БД хранится как локальное время Бишкека (без часового пояса)
            # Конвертируем дату с часовым поясом в локальное время Бишкека
            if end_date.tzinfo:
                bishkek_tz = tz.gettz('Asia/Bishkek')
                end_date_local = end_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                end_date_local = end_date
            params.append(end_date_local.strftime("%Y-%m-%d %H:%M:%S"))
        
        query += " ORDER BY date DESC"
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        conn.close()
        return results
    
    def get_total_amount(self, chat_id: int, start_date: Optional[datetime] = None,
                         end_date: Optional[datetime] = None) -> float:
        """Получает общую сумму расходов за период"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = "SELECT SUM(amount) FROM expenses WHERE chat_id = ?"
        params = [chat_id]
        
        if start_date:
            query += " AND date >= ?"
            # Время в БД хранится как локальное время Бишкека (без часового пояса)
            # Конвертируем дату с часовым поясом в локальное время Бишкека
            if start_date.tzinfo:
                bishkek_tz = tz.gettz('Asia/Bishkek')
                start_date_local = start_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                start_date_local = start_date
            params.append(start_date_local.strftime("%Y-%m-%d %H:%M:%S"))

        if end_date:
            query += " AND date <= ?"
            # Время в БД хранится как локальное время Бишкека (без часового пояса)
            # Конвертируем дату с часовым поясом в локальное время Бишкека
            if end_date.tzinfo:
                bishkek_tz = tz.gettz('Asia/Bishkek')
                end_date_local = end_date.astimezone(bishkek_tz).replace(tzinfo=None)
            else:
                end_date_local = end_date
            params.append(end_date_local.strftime("%Y-%m-%d %H:%M:%S"))
        
        cursor.execute(query, params)
        result = cursor.fetchone()
        conn.close()
        return result[0] if result[0] else 0.0

    # ----- Настройки чата (лимит расходов и т.п.) -----

    def set_daily_limit(self, chat_id: int, limit: Optional[float]) -> None:
        """
        Устанавливает суточный лимит расходов для чата.
        Если limit is None, лимит сбрасывается (будет использоваться значение по умолчанию в боте).
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        if limit is None:
            cursor.execute(
                """
                INSERT INTO chat_settings (chat_id, daily_limit)
                VALUES (?, NULL)
                ON CONFLICT(chat_id) DO UPDATE SET daily_limit = NULL
                """,
                (chat_id,),
            )
        else:
            cursor.execute(
                """
                INSERT INTO chat_settings (chat_id, daily_limit)
                VALUES (?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET daily_limit = excluded.daily_limit
                """,
                (chat_id, limit),
            )
        conn.commit()
        conn.close()

    def get_daily_limit(self, chat_id: int) -> Optional[float]:
        """Возвращает суточный лимит расходов для чата или None, если не задан."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT daily_limit FROM chat_settings WHERE chat_id = ?",
            (chat_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return row[0]

    def set_role(self, chat_id: int, user_id: int, username: str,
                 role: str, assigned_by: int) -> None:
        """Устанавливает/обновляет роль пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO roles (chat_id, user_id, username, role, assigned_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET role = excluded.role,
                          username = excluded.username,
                          assigned_by = excluded.assigned_by,
                          assigned_at = CURRENT_TIMESTAMP
        """, (chat_id, user_id, username, role, assigned_by))
        conn.commit()
        conn.close()

    def get_roles(self, chat_id: int) -> List[Tuple]:
        """Возвращает список ролей по чату"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, role, assigned_at
            FROM roles
            WHERE chat_id = ?
            ORDER BY assigned_at DESC
        """, (chat_id,))
        results = cursor.fetchall()
        conn.close()
        return results

    def get_role(self, chat_id: int, user_id: int) -> Optional[Tuple]:
        """Возвращает роль пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role FROM roles
            WHERE chat_id = ? AND user_id = ?
        """, (chat_id, user_id))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def set_name(self, chat_id: int, user_id: int, username: str,
                 name: str, assigned_by: int) -> None:
        """Устанавливает/обновляет имя пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO names (chat_id, user_id, username, name, assigned_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET name = excluded.name,
                          username = excluded.username,
                          assigned_by = excluded.assigned_by,
                          assigned_at = CURRENT_TIMESTAMP
        """, (chat_id, user_id, username, name, assigned_by))
        conn.commit()
        conn.close()
    
    def get_name(self, chat_id: int, user_id: int) -> Optional[str]:
        """Возвращает имя пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM names
            WHERE chat_id = ? AND user_id = ?
        """, (chat_id, user_id))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def get_all_info(self, chat_id: int) -> List[Tuple]:
        """Возвращает информацию о всех пользователях (роль и имя)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        # SQLite не поддерживает FULL OUTER JOIN, используем UNION
        cursor.execute("""
            SELECT 
                r.user_id,
                r.username,
                r.role,
                n.name
            FROM roles r
            LEFT JOIN names n ON r.chat_id = n.chat_id AND r.user_id = n.user_id
            WHERE r.chat_id = ?
            UNION
            SELECT 
                n.user_id,
                n.username,
                NULL as role,
                n.name
            FROM names n
            WHERE n.chat_id = ? AND n.user_id NOT IN (
                SELECT user_id FROM roles WHERE chat_id = ?
            )
            ORDER BY user_id
        """, (chat_id, chat_id, chat_id))
        results = cursor.fetchall()
        conn.close()
        return results
    
    def get_user_id_by_username(self, chat_id: int, username: str) -> Optional[int]:
        """Находит user_id по username в базе данных"""
        conn = self.get_connection()
        cursor = conn.cursor()
 
        cursor.execute("""
            SELECT user_id FROM roles
            WHERE chat_id = ? AND LOWER(username) = LOWER(?)
            LIMIT 1
        """, (chat_id, username))
        result = cursor.fetchone()
        if result:
            conn.close()
            return result[0]
 
        cursor.execute("""
            SELECT user_id FROM names
            WHERE chat_id = ? AND LOWER(username) = LOWER(?)
            LIMIT 1
        """, (chat_id, username))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def delete_expense(self, expense_id: int, chat_id: int, user_id: int = None, force: bool = False) -> bool:
        """
        Удаляет расход по ID
        :param expense_id: ID расхода
        :param chat_id: ID чата
        :param user_id: ID пользователя (если None и force=False, не проверяет владельца)
        :param force: Если True, удаляет без проверки user_id (для администраторов)
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = "DELETE FROM expenses WHERE id = ? AND chat_id = ?"
        params = [expense_id, chat_id]
     
        # Если user_id указан и не force, проверяем владельца
        if user_id is not None and not force:
            query += " AND user_id = ?"
            params.append(user_id)
        
        cursor.execute(query, params)
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    def get_expense_by_id(self, expense_id: int, chat_id: int) -> Optional[Tuple]:
        """Получает расход по ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, username, amount, description, category, message_id, date
            FROM expenses
            WHERE id = ? AND chat_id = ?
        """, (expense_id, chat_id))
        result = cursor.fetchone()
        conn.close()
        return result

        def get_expenses_by_ids(self, expense_ids: list, chat_id: int) -> list:
            """Получает список расходов по их ID"""
            if not expense_ids:
                return []
            conn = self.get_connection()
            cursor = conn.cursor()

            placeholders = ','.join(['?'] * len(expense_ids))
            query = f"""
                SELECT id, user_id, username, amount, description, category, message_id, date
                FROM expenses
                WHERE id IN ({placeholders}) AND chat_id = ?
            """
            params = expense_ids + [chat_id]
            cursor.execute(query, params)
            results = cursor.fetchall()
            conn.close()
            return results
