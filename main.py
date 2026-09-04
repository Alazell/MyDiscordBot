import os
import sys # новое
import importlib # новое
import time # новое

import discord
from discord import app_commands # новое

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not configured. Add the Discord bot token to Replit Secrets."
    )

intents = discord.Intents.all()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client) # новое

#####################################################################

# Новое (рукоделие): команда /ping - проверка работоспособности

# Команда ping
@tree.command(name="ping", description="Проверить задержку бота")
async def ping(interaction: discord.Interaction):
    # Замеряем время отправки
    start_time = time.monotonic()

    # Отправляем начальное сообщение
    await interaction.response.send_message("🏓 Проверяю задержку...")

    # Вычисляем время ответа Discord
    end_time = time.monotonic()

    # Формируем ответ с данными
    response_time = round((end_time - start_time) * 1000)  # в миллисекундах
    websocket_latency = round(client.latency * 1000)  # пинг WebSocket

    # Обновляем сообщение с результатами
    await interaction.follow_up.send(
        f"🔄 Бот ответил за {response_time} мс\n"
        f"📶 WebSocket задержка: {websocket_latency} мс"
    )

# НОВОЕ: команда /reload — перезагружает все модули без рестарта бота
@tree.command(name="reload", description="Перезагрузить все модули бота")
async def reload_modules(interaction: discord.Interaction) -> None:
    # Проверка: только владелец бота может использовать
    ALLOWED_IDS = [626052608074711040]
    if interaction.user.id not in ALLOWED_IDS:
        await interaction.response.send_message("Недостаточно прав. Это команда только для <@626052608074711040> (а ты думал?)", ephemeral=True)
        return

    reloaded = []
    errors = []

    # Перебираем все загруженные модули
    for name in list(sys.modules.keys()):
        # Пропускаем встроенные и сам main.py
        if name.startswith("_") or name in ("main", "discord", "os", "sys", "importlib"):
            continue
        # Пропускаем подмодули discord
        if name.startswith("discord"):
            continue

        try:
            module = sys.modules[name]
            # Перезагружаем только то, что реально загружено из файлов проекта
            if hasattr(module, "__file__") and module.__file__ is not None:
                importlib.reload(module)
                reloaded.append(name)
        except Exception as e:
            errors.append(f"{name}: {e}")

    # Пересинхронизируем команды после перезагрузки
    try:
        await tree.sync()
    except Exception as e:
        errors.append(f"sync: {e}")

    msg = f"Перезагружено модулей: {len(reloaded)}"
    if reloaded:
        msg += f"\n› {', '.join(reloaded)}"
    if errors:
        msg += f"\nОшибки:\n› " + "\n› ".join(errors)

    await interaction.response.send_message(msg, ephemeral=True)

#####################################################################

@client.event
async def on_ready() -> None:
    if client.user is None:
        return

    await client.change_presence(
        status=discord.Status.dnd,
        activity=discord.CustomActivity(name="Проводится техническое обслуживание. Ожидайте!"),
    )

    # НОВОЕ: синхронизируем slash-команды при старте
    try:
        await tree.sync()
        print("Slash-команды синхронизированы")
        print(tree.get_commands())
    except Exception as e:
        print(f"Ошибка синхронизации команд: {e}")
    #

    print(f"Discord-бот запущен как {client.user}")
    print('Статус профиля: "Я существую"')


if __name__ == "__main__":
    client.run(TOKEN)
