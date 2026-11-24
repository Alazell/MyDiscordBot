import os
import time
from verification import ApplicationReviewView, VerificationView, send_verification_embed
import birthday
from jun_commands import emote
from dotenv import load_dotenv
import discord
from discord.ext import tasks, commands
load_dotenv()

if not os.environ['DISCORD_TOKEN']:
    raise RuntimeError(
        "DISCORD_TOKEN is not configured."
    )

client = commands.Bot(intents=discord.Intents.all(),
                      activity=discord.CustomActivity(name="*Сладко прилизывается, подстраиваясь над вашими метаданными*"),
                      command_prefix=commands.when_mentioned_or('$'))

TEST_GUILD_ID = 1083623196164632616
TEST_GUILD = discord.Object(id=TEST_GUILD_ID)
#####################################################################
@commands.hybrid_group(name="debug", description="Отладочные команды проекта")
async def DebugGroup(ctx: commands.Context) -> None:
    pass
DebugGroup.add_command(send_verification_embed)
#####################################################################
@commands.hybrid_command(name="ping", description="Проверить задержку бота")
async def ping(ctx: commands.Context):
    start_time = time.monotonic()
    await ctx.send("🏓 Проверяю задержку...")
    end_time = time.monotonic()
    response_time = round((end_time - start_time) * 1000)
    websocket_latency = round(client.latency * 1000)

    await ctx.send(
        f"🔄 Бот ответил за {response_time} мс\n"
        f"📶 WebSocket задержка: {websocket_latency} мс")
#####################################################################
@tasks.loop(hours=1)
async def birthday_role_task() -> None:
    try: await birthday.sync_birthday_roles(client)
    except Exception as e: import logging; logging.exception(f"Ошибка синхронизации ролей дня рождения: {e}")
async def _setup_hook() -> None:
    client.add_view(VerificationView())
    client.add_view(ApplicationReviewView())

    client.add_command(DebugGroup)
    client.add_command(emote)
    client.add_command(ping)
    client.add_command(birthday.set_birthday)
    
    synced = await client.tree.sync()
    print(f"Синхронизировано команд: {len(synced)}")
    for cmd in client.tree.get_commands():
        print(f"  {cmd.name} -> {[s.name for s in getattr(cmd, 'commands', [])]}")
client.setup_hook = _setup_hook

#####################################################################
@client.event
async def on_ready() -> None:
    if client.user is None: return

    try:
        count = await birthday.init_db()
        print(f'birthdays.db загружен и имеет {count} пользователей в списке.')
    except Exception as error: print(f'Возникла ошибка при загрузке birthdays.db: {error}')
    else: 
        if not birthday_role_task.is_running(): birthday_role_task.start()
    
    print(f"Discord-бот запущен как {client.user}")

if __name__ == "__main__":
    client.run(os.environ['DISCORD_TOKEN'])
