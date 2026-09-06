import json
import re
from pathlib import Path

import discord
from discord import app_commands


APPLICATION_CATEGORY_ID = 1532132723802505286
ANSWER_STORE = Path(__file__).with_name(".verification_answers.json")
APPLICATION_TOPIC_PREFIX = "verification_application:"


def _load_previous_answers() -> dict[str, dict[str, str]]:
    try:
        with ANSWER_STORE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}


def _save_previous_answers(answers: dict[str, dict[str, str]]) -> None:
    temporary_store = ANSWER_STORE.with_suffix(".tmp")
    with temporary_store.open("w", encoding="utf-8") as file:
        json.dump(answers, file, ensure_ascii=False, indent=2)
    temporary_store.replace(ANSWER_STORE)


def _channel_name(member: discord.Member) -> str:
    display_name = member.nick or member.name
    safe_name = re.sub(r"[^\w-]+", "-", display_name, flags=re.UNICODE)
    safe_name = safe_name.strip("-_").lower() or str(member.id)
    return f"заявка-{safe_name}"[:100]


class VerificationModal(discord.ui.Modal, title="Заявка на верификацию"):
    question_1 = discord.ui.TextInput(
        label="1. Как вы относитесь к фурри? :p",
        placeholder="#нормально. #хорошо. #слишком-стесняюсь-чтобы-ответить-развёрнуто",
        style=discord.TextStyle.paragraph,
        custom_id="verification:attitude",
        max_length=1000,
    )
    question_2 = discord.ui.TextInput(
        label="2. Сколько вам лет?",
        placeholder="14 — указывайте свой настоящий возраст",
        custom_id="verification:age",
        max_length=3,
    )
    question_3 = discord.ui.TextInput(
        label="3. Каким методом вы присоединились к серверу?",
        placeholder="1 — мониторинг; 2 — приглашение; 3 — партнёрство",
        custom_id="verification:source",
        max_length=1,
    )
    question_4 = discord.ui.TextInput(
        label="Любимое пиво? :3",
        placeholder="¯\\_(ツ)_/¯",
        custom_id="verification:beer",
        max_length=200,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

        previous_answers = _load_previous_answers().get(str(user_id), {})
        for field, key in (
            (self.question_1, "attitude"),
            (self.question_2, "age"),
            (self.question_3, "source"),
            (self.question_4, "beer"),
        ):
            if previous_answers.get(key):
                field.default = previous_answers[key]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "Заявку можно отправить только на сервере.",
                ephemeral=True,
            )
            return

        age_text = self.question_2.value.strip()
        if not age_text.isdigit() or not 1 <= int(age_text) <= 120:
            await interaction.followup.send(
                "Укажите настоящий возраст числом от 1 до 120.",
                ephemeral=True,
            )
            return

        source_key = self.question_3.value.strip()
        source_names = {
            "1": "Бот мониторинга — сайт со списком серверов",
            "2": "Прямое приглашение — от друга или другого пользователя",
            "3": "Партнёр-Пиар — ссылка партнёрства с другим сервером",
        }
        if source_key not in source_names:
            await interaction.followup.send(
                "В третьем вопросе укажите только 1, 2 или 3.",
                ephemeral=True,
            )
            return

        category = interaction.guild.get_channel(APPLICATION_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                "Категория для заявок не найдена. Сообщите об этом администрации.",
                ephemeral=True,
            )
            return

        application_topic = f"{APPLICATION_TOPIC_PREFIX}{interaction.user.id}"
        existing_channel = next(
            (
                channel
                for channel in category.text_channels
                if channel.topic == application_topic
            ),
            None,
        )
        if existing_channel is not None:
            await interaction.followup.send(
                f"У вас уже есть открытая заявка: {existing_channel.mention}",
                ephemeral=True,
            )
            return

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        if interaction.guild.me is not None:
            overwrites[interaction.guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            )

        try:
            application_channel = await interaction.guild.create_text_channel(
                name=_channel_name(interaction.user),
                category=category,
                topic=application_topic,
                overwrites=overwrites,
                reason=f"Новая заявка на верификацию от {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Бот не может создать канал заявки. Проверьте его права.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Не удалось создать канал заявки. Попробуйте ещё раз позже.",
                ephemeral=True,
            )
            return

        answers = {
            "attitude": self.question_1.value.strip(),
            "age": age_text,
            "source": source_key,
            "beer": self.question_4.value.strip(),
        }
        previous_answers = _load_previous_answers()
        previous_answers[str(self.user_id)] = answers
        try:
            _save_previous_answers(previous_answers)
        except OSError:
            # Заявка уже создана; отсутствие сохранения не должно скрывать её от пользователя.
            pass

        welcome_embed = discord.Embed(
            title="Приветственная информация",
            description=(
                "*Вы отправили запрос на верификацию.*\n"
                "*Проверка осуществляется за счёт живых людей. В некоторых случаях "
                "данная ветка может разблокироваться, потому что действующий "
                "администратор захотел с вами поговорить.*\n"
                "*В остальных случаях запрос примут без вопросов.*\n\n"
                "**Пока вы ждёте:**\n"
                "- Можете ознакомиться с <#1452702255877984256>\n"
                "- Взять немного персонализирующих ролей в "
                "<#1451832635285573682>\n"
                "- *Без проверки вы не можете взять роли, дающие доступ к каналам.*\n"
                "- Расслабиться."
            ),
            color=discord.Color(11927296),
        )

        admin_embed = discord.Embed(
            title="Зона администратора",
            color=discord.Color(6360573),
        )
        admin_embed.add_field(
            name="Отношение к сообществу",
            value=answers["attitude"] or "Не указано",
            inline=False,
        )
        admin_embed.add_field(
            name="Метод присоединения",
            value=source_names[source_key],
            inline=False,
        )
        admin_embed.add_field(
            name="Возраст",
            value=f"{answers['age']}\n*Роль возраста: будет настроена отдельно*",
            inline=True,
        )
        admin_embed.add_field(
            name="Любимое пиво",
            value=answers["beer"] or "Не указано",
            inline=True,
        )

        await application_channel.send(embeds=[welcome_embed, admin_embed])
        await interaction.followup.send(
            f"Заявка создана: {application_channel.mention}",
            ephemeral=True,
        )


class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Жмяк!",
        style=discord.ButtonStyle.blurple,
        emoji="<:AE_FlowerLotus:1460321242224791676>",
        custom_id="verification:open_application",
    )
    async def button_callback(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            VerificationModal(interaction.user.id)
        )

# Временная команда для отправки эмбеда
@app_commands.command(name="embedadd", description="Временная команда для отправки сообщения верификации")
async def send_verification_embed(interaction: discord.Interaction):
    # Создаем эмбед
    embed = discord.Embed(
        title="Наш сервер - это прекрасное место чтобы расслабиться в компании друзей вечерком! 🍻 *дзынь*",
        description="Мы стремимся сохранить приятную атмосферу в нашем уютном, пивном заведении. Рады видеть тебя здесь! 🌿\n\n"
                   "<:firHype:1451491830993653760>__Жмякни на кнопочку ниже чтобы оставить заявку для входа на сервер!__ :MaysiBounce:",
        color=discord.Color.from_rgb(65, 105, 225)  # Приблизительный цвет из данных
    )
    embed.set_author(name="🐾Пивзавод🍺")
    embed.set_image(url="https://cdn.discordapp.com/attachments/1451481228275351702/1545157457305669743/ezgif-8d871781dcd69a7b.gif")

    # Отправляем сообщение
    await interaction.response.send_message(
        embed=embed,
        view=VerificationView(),
        ephemeral=False
    )

# Не забудь добавить эту команду в группу команд
class VerificationCog(discord.app_commands.Group):
    """Группа команд для верификации пользователей"""

    def __init__(self):
        super().__init__(name="верификация", description="Команды для системы верификации")
        # Добавляем новые команды сюда
        self.add_command(send_verification_embed)

    # Остальные команды...

# Временная пометка в коде
# 🏗️ ВРЕМЕННОЕ РЕШЕНИЕ: команда для тестирования эмбеда верификации
# Будет заменена на постоянную реализацию после доработки системы
