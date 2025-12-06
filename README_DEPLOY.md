# 🚀 Быстрый старт: Развертывание бота на сервере

## Самый простой способ (Railway.app - бесплатно)

1. **Зарегистрируйтесь на [Railway.app](https://railway.app)** (можно через GitHub)

2. **Создайте новый проект:**
   - Нажмите "New Project"
   - Выберите "Deploy from GitHub repo" или "Empty Project"

3. **Если используете GitHub:**
   - Подключите ваш репозиторий
   - Railway автоматически определит Python проект

4. **Если загружаете файлы вручную:**
   - Нажмите "Add Service" → "GitHub Repo" или "Empty Service"
   - Загрузите файлы: `bot.py`, `database.py`, `requirements.txt`

5. **Настройте переменные окружения:**
   - Перейдите в Settings → Variables
   - Добавьте:
     ```
     BOT_TOKEN=8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU
     ```
   - Если нужен прокси, добавьте:
     ```
     PROXY_URL=socks5://proxy.example.com:1080
     PROXY_USERNAME=username
     PROXY_PASSWORD=password
     ```

6. **Настройте команду запуска:**
   - В Settings → Deploy
   - Start Command: `python bot.py`

7. **Готово!** Бот будет работать 24/7

## Альтернатива: Render.com (тоже бесплатно)

1. Зарегистрируйтесь на [Render.com](https://render.com)
2. Создайте новый "Background Worker"
3. Подключите GitHub или загрузите файлы
4. Настройки:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
5. Добавьте переменные окружения
6. Готово!

## Использование Docker (для VPS)

Если у вас есть VPS с Linux:

```bash
# 1. Установите Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# 2. Скопируйте файлы на сервер
scp bot.py database.py requirements.txt Dockerfile docker-compose.yml user@your-server:/path/to/bot/

# 3. Создайте .env файл
echo "BOT_TOKEN=8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU" > .env

# 4. Запустите
docker-compose up -d

# 5. Проверьте логи
docker-compose logs -f
```

## Проверка работы

После развертывания отправьте боту команду `/test` в Telegram. Если бот отвечает - всё работает!

## Резервное копирование базы данных

Регулярно делайте бэкап файла `expenses.db`. На Railway/Render это можно настроить через cron или scheduled tasks.

## Нужна помощь?

Смотрите подробные инструкции в файле `DEPLOY.md`

