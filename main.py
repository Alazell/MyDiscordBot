import os
import time
from verification import ApplicationReviewView, VerificationView, send_verification_embed
import birthday
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import tasks
load_dotenv()

if not os.environ['DISCORD_TOKEN']:
    raise RuntimeError(
        "DISCORD_TOKEN is not configured."
    )

intents = discord.Intents.all()
intents.message_content = True
client = discord.Client(intents=intents, activity=discord.CustomActivity(name="*Сладко прилизывается, подстраиваясь над вашими метаданными*"))
async def _setup_hook() -> None:
    client.add_view(VerificationView())
    client.add_view(ApplicationReviewView())
client.setup_hook = _setup_hook
TEST_GUILD_ID = 1083623196164632616
TEST_GUILD = discord.Object(id=TEST_GUILD_ID)
tree = app_commands.CommandTree(client)
DebugGroup = app_commands.Group(name="debug", description="Отладочные команды проекта")
DebugGroup.add_command(send_verification_embed)

#####################################################################
@tasks.loop(hours=1)
async def birthday_role_task() -> None:
    try: await birthday.sync_birthday_roles(client)
    except Exception as e: import logging; logging.exception(f"Ошибка синхронизации ролей дня рождения: {e}")
#####################################################################
@app_commands.command(name="ping", description="Проверить задержку бота")
async def ping(interaction: discord.Interaction):
    start_time = time.monotonic()
    await interaction.response.send_message("🏓 Проверяю задержку...")
    end_time = time.monotonic()
    response_time = round((end_time - start_time) * 1000)
    websocket_latency = round(client.latency * 1000)

    await interaction.followup.send(
        f"🔄 Бот ответил за {response_time} мс\n"
        f"📶 WebSocket задержка: {websocket_latency} мс")
#####################################################################
@client.event
async def on_ready() -> None:
    if client.user is None: return
    try:
        tree.clear_commands(guild=TEST_GUILD)
        await tree.sync(guild=TEST_GUILD)
        synced = await tree.sync()
        print(f"Синхронизировано команд: {len(synced)}")
        print([(command.name, command.id) for command in synced])
    except Exception as error: print(f"Ошибка синхронизации команд: {error}")

    try:
        count = await birthday.init_db()
        print(f'birthdays.db загружен и имеет {count} пользователей в списке.')
    except Exception as error: print(f'Возникла ошибка при загрузке birthdays.db: {error}')
    else: 
        if not birthday_role_task.is_running(): birthday_role_task.start()
    
    print(f"Discord-бот запущен как {client.user}")
    print('Статус профиля: "Я существую"')

tree.add_command(DebugGroup)
from jun_commands import emote
tree.add_command(emote)
tree.add_command(ping)
tree.add_command(birthday.set_birthday)
if __name__ == "__main__":
    client.run(os.environ['DISCORD_TOKEN'])
