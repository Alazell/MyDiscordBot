import os
import time
from verification import ApplicationReviewView, VerificationView, send_verification_embed
import birthday
import color_roles
from jun_commands import emote
from dotenv import load_dotenv
import discord
from discord.ext import tasks, commands
import logging
load_dotenv()

if not os.getenv('DISCORD_TOKEN'): raise RuntimeError("DISCORD_TOKEN is not configured.")
client = commands.Bot(intents=discord.Intents.all(),
                      activity=discord.CustomActivity(name="*Сладко прилизывается, подстраиваясь над вашими метаданными*"),
                      command_prefix=commands.when_mentioned_or('$'),
                      help_command=None)
TEST_GUILD_ID = 1083623196164632616
TEST_GUILD = discord.Object(id=TEST_GUILD_ID)
#####################################################################
@commands.hybrid_group(name="debug", description="Отладочные команды проекта")
async def DebugGroup(ctx: commands.Context) -> None: pass
DebugGroup.add_command(send_verification_embed)


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

@commands.hybrid_command(name="help", description="Показать список доступных команд бота")
async def help_command(ctx: commands.Context) -> None:
    try: prefixes = await client.get_prefix(ctx.message); prefix = prefixes[-1] if isinstance(prefixes, list) and prefixes else (prefixes or "$")
    except Exception: prefix = "Отсутствует / Не определено"
    embed = discord.Embed(
        title="Список доступных команд",
        description=f'''Префикс — `{prefix}`
                        либо слеш — `/`
                        либо упоминание — {client.user.mention if client.user else ''}''',
        colour=discord.Colour.blurple())
    lines: list[str] = []
    for cmd in sorted(client.commands, key=lambda c: c.name):
        if cmd.name == "help": continue
        desc = (cmd.description or cmd.brief or cmd.help or "описание не задано").strip().splitlines()[0]
        sig = str(cmd.signature).strip()
        sig_part = f" `{sig}`" if sig else ""
        if isinstance(cmd, commands.Group):
            lines.append(f"> **{cmd.name}**{sig_part}")
            lines.append(f"> -# {desc}")
            for sub in sorted(cmd.commands, key=lambda c: c.name):
                sub_sig = str(sub.signature).strip()
                sub_desc = (sub.description or sub.brief or sub.help or "описание не задано").strip().splitlines()[0]
                sub_sig_part = f" `{sub_sig}`" if sub_sig else ""
                lines.append(f"> - **{sub.name}**{sub_sig_part}")
                lines.append(f">   -# {sub_desc}")
        else:
            lines.append(f"- **{cmd.name}**{sig_part}")
            lines.append(f"  -# {desc}")
    values: list[str] = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > 1000:
            values.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current: values.append(current)
    for value in values: embed.add_field(name="\u200b", value=value, inline=False)
    try: await ctx.send(embed=embed)
    except discord.Forbidden: await ctx.send(content='У приложения нету прав отправить это сообщение. вы не дали право на вложения?')
    except Exception: logging.exception("Не удалось отправить embed help")

#####################################################################
@tasks.loop(hours=1)
async def birthday_role_task() -> None:
    try: await birthday.sync_birthday_roles(client)
    except Exception: logging.exception("Ошибка синхронизации ролей дня рождения")
@tasks.loop(hours=1)
async def color_roles_task() -> None:
    try: await color_roles.sync_all(client)
    except Exception: logging.exception("Ошибка синхронизации цветных ролей")
async def _setup_hook() -> None:
    client.add_view(VerificationView())
    client.add_view(ApplicationReviewView())

    client.add_command(DebugGroup)
    client.add_command(emote)
    client.add_command(ping)
    client.add_command(birthday.set_birthday)
    client.add_command(help_command)
    client.add_command(color_roles.color_command)

    try:
        synced = await client.tree.sync()
        print(f"Синхронизировано команд: {len(synced)}")
        for cmd in client.tree.get_commands():
            print(f"  {cmd.name} -> {[s.name for s in getattr(cmd, 'commands', [])]}")
    except discord.HTTPException:
        logging.exception("Глобальный синк не удался — бот работает, команды на стороне Discord не обновлены")
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
    
    try: await color_roles.init_db()
    except Exception as error: print(f'Возникла ошибка при загрузке color_roles.db: {error}')
    
    else:
        if not color_roles_task.is_running(): color_roles_task.start()
    
    print(f"Discord-бот запущен как {client.user}")

if __name__ == "__main__":
    client.run(os.environ['DISCORD_TOKEN'])
