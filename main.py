import os
import sys
import time
from pathlib import Path
from verification import ApplicationReviewView, DebugGroup, VerificationView
from debug import ping, reload_modules
from dotenv import load_dotenv
import discord
import importlib
from discord import app_commands
load_dotenv()

__all__ = [
    'os',
    'sys',
    'time',
    'Path',
    'discord',
    'importlib',
    'app_commands',
    'load_dotenv',
    'VerificationView',
    'ApplicationReviewView',
    'DebugGroup',
]

if not os.environ['DISCORD_TOKEN']:
    raise RuntimeError(
        "DISCORD_TOKEN is not configured."
    )



intents = discord.Intents.all()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
tree.add_command(DebugGroup())

client.tree = tree
tree.add_command(ping)
tree.add_command(reload_modules)

async def _setup_hook() -> None:
    client.add_view(VerificationView())
    client.add_view(ApplicationReviewView())
client.setup_hook = _setup_hook

#####################################################################
TEST_GUILD_ID = 1017754395968012308
TEST_GUILD = discord.Object(id=TEST_GUILD_ID)
#####################################################################

# Команда ping


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
    client.run(os.environ['DISCORD_TOKEN'])
