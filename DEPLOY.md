# 🚀 Развертывание бота на сервере

Этот документ содержит инструкции по развертыванию бота на различных платформах, чтобы он работал 24/7.

## 📋 Варианты развертывания

### 1. Docker (рекомендуется)

#### Локальный сервер/VPS с Docker

1. **Установите Docker и Docker Compose:**
   ```bash
   # Ubuntu/Debian
   curl -fsSL https://get.docker.com -o get-docker.sh
   sh get-docker.sh
   sudo apt-get install docker-compose-plugin
   ```

2. **Скопируйте файлы на сервер:**
   - `bot.py`
   - `database.py`
   - `requirements.txt`
   - `Dockerfile`
   - `docker-compose.yml`

3. **Создайте файл `.env` с переменными окружения:**
   ```bash
   BOT_TOKEN=8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU
   PROXY_URL=  # Опционально
   PROXY_USERNAME=  # Опционально
   PROXY_PASSWORD=  # Опционально
   ```

4. **Запустите бота:**
   ```bash
   docker-compose up -d
   ```

5. **Проверьте логи:**
   ```bash
   docker-compose logs -f
   ```

6. **Остановка:**
   ```bash
   docker-compose down
   ```

### 2. Railway.app (бесплатный вариант)

1. Зарегистрируйтесь на [Railway.app](https://railway.app)
2. Создайте новый проект
3. Подключите GitHub репозиторий или загрузите файлы
4. Добавьте переменные окружения:
   - `BOT_TOKEN`
   - `PROXY_URL` (если нужно)
5. Railway автоматически определит Python проект и запустит бота
6. Бот будет работать 24/7

### 3. Render.com (бесплатный вариант)

1. Зарегистрируйтесь на [Render.com](https://render.com)
2. Создайте новый "Web Service"
3. Подключите GitHub репозиторий
4. Настройки:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Environment:** Python 3
5. Добавьте переменные окружения в разделе "Environment"
6. Бот будет работать 24/7 (на бесплатном плане может "засыпать" после 15 минут бездействия)

### 4. Heroku

1. Установите [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)
2. Создайте файл `Procfile`:
   ```
   worker: python bot.py
   ```
3. Войдите в Heroku:
   ```bash
   heroku login
   ```
4. Создайте приложение:
   ```bash
   heroku create your-bot-name
   ```
5. Установите переменные окружения:
   ```bash
   heroku config:set BOT_TOKEN=8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU
   ```
6. Разверните:
   ```bash
   git push heroku main
   ```

### 5. VPS с systemd (Linux)

1. **Создайте systemd сервис** `/etc/systemd/system/expenses-bot.service`:
   ```ini
   [Unit]
   Description=Expenses Bot
   After=network.target

   [Service]
   Type=simple
   User=your-user
   WorkingDirectory=/path/to/expenses
   Environment="BOT_TOKEN=8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU"
   Environment="PROXY_URL="
   ExecStart=/usr/bin/python3 /path/to/expenses/bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

2. **Активируйте сервис:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable expenses-bot
   sudo systemctl start expenses-bot
   ```

3. **Проверьте статус:**
   ```bash
   sudo systemctl status expenses-bot
   ```

4. **Просмотр логов:**
   ```bash
   sudo journalctl -u expenses-bot -f
   ```

### 6. PythonAnywhere (бесплатный вариант)

1. Зарегистрируйтесь на [PythonAnywhere](https://www.pythonanywhere.com)
2. Загрузите файлы через веб-интерфейс или Git
3. Создайте задачу (Task) в разделе "Tasks"
4. Команда: `python3.10 bot.py`
5. Установите переменные окружения в разделе "Environment variables"

## 🔧 Настройка переменных окружения

На всех платформах нужно установить:

- `BOT_TOKEN` - токен бота (обязательно)
- `PROXY_URL` - URL прокси (опционально)
- `PROXY_USERNAME` - логин прокси (опционально)
- `PROXY_PASSWORD` - пароль прокси (опционально)

## 📊 Мониторинг

### Проверка работы бота

1. Отправьте команду `/test` боту в Telegram
2. Проверьте логи на платформе
3. Убедитесь, что база данных сохраняется

### Резервное копирование

Регулярно делайте резервные копии файла `expenses.db`:

```bash
# Ручное копирование
cp expenses.db expenses.db.backup

# Автоматическое копирование (cron)
0 2 * * * cp /path/to/expenses.db /path/to/backup/expenses-$(date +\%Y\%m\%d).db
```

## 🆘 Решение проблем

### Бот не отвечает

1. Проверьте логи на платформе
2. Убедитесь, что токен правильный
3. Проверьте подключение к интернету/прокси

### База данных не сохраняется

1. Убедитесь, что файл `expenses.db` доступен для записи
2. Проверьте права доступа к файлу
3. При использовании Docker убедитесь, что том смонтирован правильно

### Бот падает

1. Проверьте логи на наличие ошибок
2. Убедитесь, что все зависимости установлены
3. Проверьте, что переменные окружения установлены правильно

## 💡 Рекомендации

- **Для начала:** Используйте Railway.app или Render.com - они бесплатные и простые
- **Для продакшена:** Используйте VPS с Docker или systemd
- **Для масштабирования:** Используйте Docker Swarm или Kubernetes

