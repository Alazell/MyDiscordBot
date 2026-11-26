import aiosqlite
import discord
import datetime
import os
import logging
from discord.ext import commands
import notifications

async def init_db() -> int:
    async with aiosqlite.connect("birthdays.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                day TEXT NOT NULL,
                month TEXT NOT NULL)""")
        await db.commit()
        async with db.execute("SELECT COUNT(user_id) FROM birthdays") as cursor:
            (count,) = await cursor.fetchone()
    return count

@commands.hybrid_command(name="дата-рождения", description="Проверяет/изменяет дату рождения тебя/участника")
async def set_birthday(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    is_self = target.id == ctx.author.id
    ephemeral = ctx.interaction is not None

    class ChangeBirthdayModal(discord.ui.Modal, title="Изменение даты рождения"):
        Date_Input = discord.ui.Label(
            text="Введите новое значение в формате ДД.ММ",
            component=discord.ui.TextInput(
                placeholder="05.10",
                style=discord.TextStyle.paragraph,
                custom_id="Date_Input:birthday",
                min_length=5,
                max_length=5))
        async def on_submit(self, interaction: discord.Interaction) -> None:
            try:
                day, month = map(int, self.Date_Input.component.value.strip().split("."))
                datetime.datetime(2000, month, day)
            except ValueError:
                await interaction.response.send_message("Неверная дата. Используйте формат ДД.ММ и убедитесь в существовании самой даты", ephemeral=True)
                return
            async with aiosqlite.connect("birthdays.db") as db:
                await db.execute("INSERT INTO birthdays VALUES (?, ?, ?)" "ON CONFLICT(user_id) DO UPDATE SET day = ?, month = ?", (interaction.user.id, day, month, day, month))
                await db.commit()
                await interaction.response.send_message("Дата успешно изменена", ephemeral=True)

    class ChangeBirthdayView(discord.ui.View):
            def __init__(self, target_id: int):
                super().__init__(timeout=120)
                self.target_id = target_id
                self.change.disabled = target_id != ctx.author.id
            @discord.ui.button(label="Изменить дату", style=discord.ButtonStyle.secondary)
            async def change(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.target_id:
                    await interaction.response.send_message(
                        "Вы не можете изменять чужую дату. Используйте команду в отношении себя для изменения.", ephemeral=True)
                    return
                await interaction.response.send_modal(ChangeBirthdayModal())

    async with aiosqlite.connect("birthdays.db") as db:
        async with db.execute("SELECT day, month FROM birthdays WHERE user_id = ?", (target.id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
                if is_self: await ctx.send(embed = discord.Embed(
                    color= discord.Colour.from_str('#0071ff'),
                    title= f'Дата рождения не обнаружена.',
                    description= f'Воспользуйтесь кнопкой чтобы настроить значения.')
                    .set_author(name=target.name, url=f'https://discord.com/users/{target.id}', icon_url=target.display_avatar.url),
                    view=ChangeBirthdayView(target.id))
                else: await ctx.send(
                    f"У **{target.display_name}** ещё не указана дата рождения", ephemeral=True)
                return
        day, month = int(row[0]), int(row[1])

    now = datetime.datetime.now()
    try: today = now.replace(month=month, day=day, hour=0, minute=0, second=0) #день рождения в этом году
    except ValueError: today = datetime.datetime(now.year, 3, 1)
    if today.date() == now.date(): next=now
    elif today > now: next=today
    else:
        try: next = now.replace(year=now.year+1, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
        except ValueError: next = datetime.datetime(now.year+1, 3, 1)
    delta = next-now
    if int(delta.total_seconds()) == 0: count_day, count_hour, count_min, count_sec = 0,0,0,0
    else:
        count_day = int(delta.total_seconds()) // 86400
        count_hour = (int(delta.total_seconds()) % 86400) //3600
        count_min = (int(delta.total_seconds()) % 3600) // 60
        count_sec = int(delta.total_seconds()) % 60
    if int(delta.total_seconds()) == 0: title = f"—  СЕГОДНЯ! 🎉"
    else: title = f"—  ЧЕРЕЗ {count_day} ДНЕЙ"

    await ctx.send(embed=discord.Embed(
        color= discord.Colour.from_str('#0071ff'),
        title= f'{day:02d}.{month:02d}  {title}',
        description= f'А так же `{count_hour}` часов, `{count_min}` минут и `{count_sec}` секунд.')
        .set_author(name=target.name, url=f'https://discord.com/users/{target.id}', icon_url=target.display_avatar.url)
        .set_footer(text= 'Совершили ошибку? ↙Введите новое значение по кнопке!'),
        view=ChangeBirthdayView(target.id))

async def sync_birthday_roles(client: discord.Client) -> None:
    logger = logging.getLogger(__name__)
    raw_role_id = os.getenv("BIRTHDAY_ROLE_ID", "").strip()
    try:
        role_id = int(raw_role_id)
        if role_id <= 0: raise ValueError
    except ValueError:
        logger.error("BIRTHDAY_ROLE_ID отсутствует или содержит неверный ID")
        return

    # ID роли определяет сервер, на котором задача работает с участниками.
    guild = None
    role = None

    for candidate in client.guilds:
        if candidate.unavailable: continue
        candidate_role = candidate.get_role(role_id)
        if candidate_role is not None:
            guild = candidate
            role = candidate_role
            break

    if guild is None or role is None:
        logger.warning("Роль %s не найдена на доступных серверах", role_id)
        return
    if role.is_default() or role.managed:
        logger.error("Роль %s нельзя использовать для дней рождения", role_id)
        return

    async with aiosqlite.connect("file:birthdays.db?mode=ro", uri=True) as db:
        async with db.execute("SELECT user_id, day, month FROM birthdays") as cursor: rows = await cursor.fetchall()
    today = datetime.date.today()

    for user_id, raw_day, raw_month in rows:
        try:
            user_id = int(user_id)
            day = int(raw_day)
            month = int(raw_month)
            if user_id <= 0: raise ValueError
            datetime.date(2000, month, day) # 2000 год високосный: проверка допускает 29 февраля.
        except (TypeError, ValueError, OverflowError):
            logger.warning("Некорректная дата для user_id=%r", user_id)
            continue

        if month == 2 and day == 29:
            try:birthday_this_year = datetime.date(today.year, 2, 29)
            except ValueError: birthday_this_year = datetime.date(today.year, 3, 1)
        else: birthday_this_year = datetime.date(today.year, month, day)
        should_have_role = today == birthday_this_year
        try: # Получаем участника через API, а не только из кэша.
            member = await guild.fetch_member(user_id)
            has_role = role in member.roles

            if should_have_role and not has_role:
                await member.add_roles(role, reason="День рождения")
                await notifications.announce_birthday(member)
            elif not should_have_role and has_role: await member.remove_roles(role, reason="День рождения закончился")
        except discord.NotFound: logger.info("Объект Discord не найден для user_id=%s", user_id)
        except discord.HTTPException: logger.exception("Не удалось обновить роль для user_id=%s", user_id)
