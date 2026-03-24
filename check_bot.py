
import os

import asyncio

from telegram import Bot

TOKEN_IN_CODE = "8137903259:AAG0VVcKfLDcOBdHQT_ADIR8s5daVu69eqU"

TOKEN_FROM_ENV = os.getenv("BOT_TOKEN", None)

print("=" * 60)

print("ПРОВЕРКА ТОКЕНА БОТА")

print("=" * 60)

print(f"Токен в коде: {TOKEN_IN_CODE[:20]}...")

if TOKEN_FROM_ENV:

    print(f"Токен из env: {TOKEN_FROM_ENV[:20]}...")

    print("⚠️  ВНИМАНИЕ: Переменная окружения переопределяет токен из кода!")

    token_to_use = TOKEN_FROM_ENV

else:

    print("Переменная окружения BOT_TOKEN не установлена")

    token_to_use = TOKEN_IN_CODE

print(f"\nИспользуемый токен: {token_to_use[:20]}...")

print("\nПроверка подключения к Telegram API...")

async def check_bot():

    try:

        bot = Bot(token=token_to_use)

        me = await bot.get_me()

        print(f"✅ Бот подключен!")

        print(f"   Имя: {me.first_name}")

        print(f"   Username: @{me.username}")

        print(f"   ID: {me.id}")

        webhook_info = await bot.get_webhook_info()

        if webhook_info.url:

            print(f"\n⚠️  ВНИМАНИЕ: Установлен webhook: {webhook_info.url}")

            print("   Это может мешать работе polling. Удалите webhook командой:")

            print(f"   await bot.delete_webhook()")

        else:

            print("\n✅ Webhook не установлен (polling может работать)")

    except Exception as e:

        print(f"❌ Ошибка: {e}")

        print("\nВозможные причины:")

        print("1. Неправильный токен")

        print("2. Нет подключения к интернету")

        print("3. Telegram API заблокирован (нужен прокси)")

asyncio.run(check_bot())
