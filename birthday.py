import aiosqlite
import discord
from discord import app_commands
from datetime import datetime

DB_PATH = "birthdays.db"
async def init_db() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                day TEXT NOT NULL,
                month TEXT NOT NULL)""")
        await db.commit()
        async with db.execute("SELECT COUNT(user_id) FROM birthdays") as cursor:
            (count,) = await cursor.fetchone()
    return count
    

@app_commands.command(name="дата-рождения", description="Проверяет/изменяет дату рождения тебя/участника")
async def birthday(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    is_self = target.id == interaction.user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT day, month FROM birthdays WHERE user_id = ?", (target.id,)) as cursor:
            row = await cursor.fetchone()
        day, month = row

    now = datetime.now()
    today = now.replace(month=month, day=day) #день рождения в этом году
    if today > now: next=today
    elif today == now.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=0): next=now
    else:
        try: next = now.replace(year=now.year+1, month=month, day=day)
        except ValueError: next = datetime(now.year+1, 3, 1)
    delta = next-now
    if int(delta.total_seconds()) == 0: count_day, count_hour, count_min, count_sec = 0,0,0,0
    else:
        count_day = int(delta.total_seconds()) // 86400
        count_hour = (int(delta.total_seconds()) % 86400) //3600
        count_min = (int(delta.total_seconds()) % 3600) // 60
        count_sec = int(delta.total_seconds()) % 60
    if int(delta.total_seconds()) == 0: title = f"—  СЕГОДНЯ! 🎉"
    else: title = f"—  ЧЕРЕЗ {count_day} ДНЕЙ"

    class ChangeBirthdayModal(discord.ui.Modal, title="Изменение даты рождения"):
        Date_Input = discord.ui.Label(
            text="Введите новое значение в формате ДД.ММ",
            component=discord.ui.TextInput(
                placeholder="05.10",
                style=discord.TextStyle.paragraph,
                custom_id="Date_Input:birthday",
                min_length=5,
                max_length=5))
    class ChangeBirthdayView(discord.ui.View):
        def __init__(self, target_id: int):
            super().__init__(timeout=120)
            self.target_id = target_id
            self.change.disabled = target_id != interaction.user.id
        @discord.ui.button(label="Изменить дату", style=discord.ButtonStyle.secondary)
        async def change(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.target_id:
                await interaction.response.send_message(
                    "Вы не можете изменять чужую дату. Используйте команду в отношении себя для изменения.", ephemeral=True)
                return
            await interaction.response.send_modal(ChangeBirthdayModal())


    if row is None:
        if is_self: await interaction.response.send_message(embed = discord.Embed(
                color= discord.Color.from_str('0071ff'),
                title= f'Дата рождения не обнаружена.',
                description= f'Воспользуйтесь кнопкой чтобы настроить значения.')
                .set_author(name=target.name, url=f'https://discord.com/users/{target.id}'),
                view=ChangeBirthdayView(target.id))
        else: await interaction.response.send_message(
            f"У **{target.display_name}** ещё не указана дата рождения", ephemeral=True)
    else: await interaction.response.send_message(embed=discord.Embed(
        color= discord.Color.from_str('0071ff'),
        title= f'{day:02d}.{month:02d}  {title}',
        description= f'А так же `{count_hour}` часов, `{count_min}` минут и `{count_sec}` секунд.')
        .set_author(name=target.name, url=f'https://discord.com/users/{target.id}')
        .set_footer(text= 'Совершили ошибку? ↙Введите новое значение по кнопке!'),
        view=ChangeBirthdayView(target.id))