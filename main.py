import os
import sys
import importlib
import time
from pathlib import Path
from verification import ApplicationReviewView, DebugGroup, VerificationView

import discord
from discord import app_commands

TOKEN = os.getenv("DISCORD_TOKEN")


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not configured. Add the Discord bot token to Replit Secrets."
    )

intents = discord.Intents.all()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
tree.add_command(DebugGroup())

async def _setup_hook() -> None:
    client.add_view(VerificationView())
    client.add_view(ApplicationReviewView())
client.setup_hook = _setup_hook

#####################################################################
TEST_GUILD_ID = 1017754395968012308
TEST_GUILD = discord.Object(id=TEST_GUILD_ID)
#####################################################################

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
    await interaction.followup.send(
        f"🔄 Бот ответил за {response_time} мс\n"
        f"📶 WebSocket задержка: {websocket_latency} мс"
    )
@tree.command(name="reload", description="Перезагрузить все модули бота")
async def reload_modules(interaction: discord.Interaction) -> None:
    allowed_ids = (626052608074711040,)

    if interaction.user.id not in allowed_ids:
        await interaction.response.send_message(
            "Недостаточно прав.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    project_dir = Path(__file__).resolve().parent
    dependency_dir = project_dir / ".pythonlibs"
    reloaded: list[str] = []
    errors: list[str] = []

    for name, module in list(sys.modules.items()):
        if name in {"__main__", "main"} or module is None:
            continue

        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue

        try:
            module_path = Path(module_file).resolve()
        except OSError as error:
            errors.append(f"{name}: не удалось определить путь: {error}")
            continue

        # Не перезагружать discord.py, aiohttp и другие зависимости.
        if (
            not module_path.is_relative_to(project_dir)
            or module_path.is_relative_to(dependency_dir)
            or "site-packages" in module_path.parts
        ):
            continue

        try:
            importlib.reload(module)
            reloaded.append(name)
        except Exception as error:
            errors.append(f"{name}: {error}")

    try:
        await tree.sync()
    except Exception as error:
        errors.append(f"sync: {error}")

    lines = [f"Перезагружено модулей: {len(reloaded)}"]
    if reloaded:
        lines.append("Перезагружены:\n" + "\n".join(f"• {name}" for name in reloaded))
    if errors:
        lines.append("Ошибки:\n" + "\n".join(f"• {error}" for error in errors))

    for message in lines:
        while message:
            chunk = message[:2000]
            split_at = chunk.rfind("\n")

            if len(message) > 2000 and split_at > 0:
                chunk = message[:split_at]

            await interaction.followup.send(chunk, ephemeral=True)
            message = message[len(chunk):].lstrip("\n")

#####################################################################

@client.event
async def on_ready() -> None:
    if client.user is None:
        return

    await client.change_presence(
        status=discord.Status.dnd,
        activity=discord.CustomActivity(name="Проводится техническое обслуживание. Ожидайте!"),
    )

    try:
        # Удаляем старые guild-команды, оставшиеся от предыдущей регистрации.
        # Актуальные команды публикуются глобально через следующий sync().
        tree.clear_commands(guild=TEST_GUILD)
        await tree.sync(guild=TEST_GUILD)

        synced = await tree.sync()
        print(f"Синхронизировано команд: {len(synced)}")
        print([(command.name, command.id) for command in synced])
    except Exception as error:
        print(f"Ошибка синхронизации команд: {error}")
    #

    print(f"Discord-бот запущен как {client.user}")
    print('Статус профиля: "Я существую"')


if __name__ == "__main__":
    client.run(TOKEN)
