import io
import json
import re
from dotenv import load_dotenv
from html import escape
from pathlib import Path
import os
import discord
from discord import app_commands
load_dotenv()


ANSWER_STORE = Path(__file__).with_name(".verification_answers.json")
APPLICATION_TOPIC_PREFIX = "verification_application:"
INVITE_SNAPSHOTS: dict[int, dict[str, int]] = {}


def _load_previous_answers() -> dict[str, dict[str, str]]:
    """Прочитать сохранённые ответы или вернуть пустой словарь при ошибке чтения."""
    try:
        with ANSWER_STORE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}

def _channel_name(member: discord.Member) -> str:
    """Сформировать имя канала заявки из имени участника длиной до 100 символов."""
    display_name = member.nick or member.name
    safe_name = re.sub(r"[^\w-]+", "-", display_name, flags=re.UNICODE)
    safe_name = safe_name.strip("-_").lower() or str(member.id)
    return f"заявка-{safe_name}"[:100]


def _applicant_id(channel: discord.TextChannel) -> int | None:
    """Извлечь ID заявителя из темы канала или вернуть None при неверном формате."""
    topic = channel.topic or ""
    if not topic.startswith(APPLICATION_TOPIC_PREFIX):
        return None

    try:
        return int(topic.removeprefix(APPLICATION_TOPIC_PREFIX))
    except ValueError:
        return None


def _has_review_role(interaction: discord.Interaction) -> bool:
    """Проверить наличие роли модератора верификации у автора взаимодействия."""
    return isinstance(interaction.user, discord.Member) and any(
        role.id == int(os.environ['REVIEW_ROLE_ID']) for role in interaction.user.roles
    )


async def _set_conversation_access(
    channel: discord.TextChannel,
    applicant: discord.Member,
    *,
    enabled: bool,
) -> None:
    """Настроить переписку; при отключении удалить персональные права заявителя."""
    review_role = channel.guild.get_role(int(os.environ['REVIEW_ROLE_ID']))
    if review_role is None:
        raise RuntimeError(f"Роль модераторов не найдена: {os.environ['REVIEW_ROLE_ID']}")

    await channel.set_permissions(
        review_role,
        view_channel=True,
        read_message_history=True,
        send_messages=enabled,
    )
    if enabled:
        await channel.set_permissions(
            applicant,
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
        )
    else:
        await channel.set_permissions(applicant, overwrite=None)


async def _lock_application(
    channel: discord.TextChannel,
    applicant: discord.Member | None,
) -> None:
    """Запретить сообщения роли модераторов и удалить персональные права заявителя."""
    review_role = channel.guild.get_role(int(os.environ['REVIEW_ROLE_ID']))
    if review_role is not None:
        await channel.set_permissions(
            review_role,
            view_channel=True,
            read_message_history=True,
            send_messages=False,
        )
    if applicant is not None:
        await channel.set_permissions(applicant, overwrite=None)


async def _render_transcript(channel: discord.TextChannel) -> str:
    """Вернуть HTML-транскрипт истории канала с экранированием текста."""
    messages = [
        message async for message in channel.history(limit=None, oldest_first=True)
    ]
    rendered_messages: list[str] = []

    for message in messages:
        author = escape(f"{message.author.display_name} ({message.author.id})")
        created_at = escape(message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
        content = escape(message.content).replace("\n", "<br>")
        if not content:
            content = "<em>без текста</em>"

        embeds: list[str] = []
        for embed in message.embeds:
            embed_data = embed.to_dict()
            embed_parts: list[str] = []
            if embed_data.get("title"):
                embed_parts.append(f"<strong>{escape(str(embed_data['title']))}</strong>")
            if embed_data.get("description"):
                embed_parts.append(
                    escape(str(embed_data["description"])).replace("\n", "<br>")
                )
            for field in embed_data.get("fields", []):
                embed_parts.append(
                    f"<strong>{escape(str(field.get('name', '')))}</strong><br>"
                    f"{escape(str(field.get('value', ''))).replace(chr(10), '<br>')}"
                )
            if embed_parts:
                embeds.append(f"<div class=\"embed\">{'<br>'.join(embed_parts)}</div>")

        attachments = "".join(
            f'<li><a href="{escape(attachment.url)}">{escape(attachment.filename)}</a></li>'
            for attachment in message.attachments
        )
        attachment_block = f"<ul>{attachments}</ul>" if attachments else ""
        rendered_messages.append(
            f'<article class="message">'
            f'<div class="meta"><strong>{author}</strong> · {created_at}</div>'
            f'<div class="content">{content}</div>'
            f"{''.join(embeds)}{attachment_block}"
            f"</article>"
        )

    return (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<title>Транскрипт заявки</title>"
        "<style>"
        "body{background:#313338;color:#dbdee1;font:15px Arial,sans-serif;"
        "margin:0;padding:24px}.container{max-width:900px;margin:auto}"
        ".message{background:#2b2d31;border-radius:8px;padding:12px 16px;"
        "margin:0 0 10px}.meta{color:#b5bac1;font-size:13px;margin-bottom:6px}"
        ".content{white-space:normal;overflow-wrap:anywhere}.embed{"
        "border-left:4px solid #5865f2;background:#1e1f22;padding:10px;"
        "margin-top:10px;border-radius:4px}a{color:#00a8fc}"
        "</style></head><body><main class=\"container\">"
        f"<h1>Транскрипт #{escape(channel.name)}</h1>"
        f"{''.join(rendered_messages)}"
        "</main></body></html>"
    )


class VerificationModal(discord.ui.Modal, title="Заявка на верификацию"):
    question_1 = discord.ui.Label(
        text="1. Как вы относитесь к фурри? :p",
        component=discord.ui.TextInput(
            placeholder="#нормально. #хорошо. #слишком-стесняюсь-чтобы-ответить-развёрнуто",
            style=discord.TextStyle.paragraph,
            custom_id="verification:attitude",
            min_length=5,
            max_length=750,
        ),
    )
    question_2 = discord.ui.Label(
        text="2. Сколько вам лет?",
        description="Называйте свой настоящий возраст.",
        component=discord.ui.TextInput(
            placeholder="14",
            custom_id="verification:age",
            min_length=2,
            max_length=2,
        ),
    )
    question_3 = discord.ui.Label(
        text="Каким методом вы присоединились к серверу?",
        description="Вся информация проверяется.",
        component=discord.ui.Select(
            custom_id="verification:source",
            placeholder="Выберите способ присоединения",
            options=[
                discord.SelectOption(
                    label="Бот мониторинга",
                    value="monitoring",
                    description="сайт со списком серверов",
                ),
                discord.SelectOption(
                    label="Прямое приглашение",
                    value="direct",
                    description="от друга или ещё кого-то",
                ),
                discord.SelectOption(
                    label="Партнёр-Пиар",
                    value="partnership",
                    description="ссылка партнёрства с другим сервером",
                ),
            ],
            min_values=1,
            max_values=1,
            required=True,
        ),
    )
    question_4 = discord.ui.Label(
        text="Как вы относитесь к ролевым играм?",
        description="Text RP, NSFW или SFW.",
        component=discord.ui.TextInput(
            custom_id="verification:role_mind",
            max_length=750
        )
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "Заявку можно отправить только на сервере.",
                ephemeral=True,
            )
            return
        attitude = self.question_1.component.value.strip()
        age_text = self.question_2.component.value.strip()
        if not age_text.isdigit() or not 1 <= int(age_text) <= 120:
            await interaction.followup.send(
                "Укажите настоящий возраст.",
                ephemeral=True,
            )
            return

        source_values = self.question_3.component.values
        source_key = source_values[0] if source_values else ""
        source_names = {
            "monitoring": "Бот мониторинга — сайт со списком серверов",
            "direct": "Прямое приглашение — от друга или другого пользователя",
            "partnership": "Партнёр-Пиар — ссылка партнёрства с другим сервером",
        }
        role_mind = self.question_4.component.value.strip()
        if source_key not in source_names:
            await interaction.followup.send(
                "Выберите способ присоединения к серверу.",
                ephemeral=True,
            )
            return

        category = interaction.guild.get_channel(int(os.environ['APPLICATION_CATEGORY_ID']))
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
        }
        review_role = interaction.guild.get_role(int(os.environ['REVIEW_ROLE_ID']))
        if review_role is None:
            await interaction.followup.send(
                "Роль модераторов не найдена. Сообщите об этом администрации.",
                ephemeral=True,
            )
            return

        overwrites[review_role] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
        )
        overwrites[interaction.user] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
        )
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
            "attitude": attitude,
            "age": age_text,
            "source": source_key,
            "role_mind": role_mind
        }
        previous_answers = _load_previous_answers()
        previous_answers[str(interaction.user.id)] = answers

        welcome_embed = discord.Embed(
            title="Приветственная информация",
            description=(
                "*Вы отправили запрос на верификацию.*\n"
                "*Проверка осуществляется за счёт живых людей. В некоторых случаях "
                "данная ветка может разблокироваться, потому что действующий "
                "администратор захотел с вами поговорить.*\n"
                "*В остальных случаях - запрос примут без вопросов.*\n\n"
                "**Пока вы ждёте:**\n"
                "- Можете ознакомиться с <#1086650486041616395>\n"
                "- Взять немного персонализирующих ролей в "
                "<#1084044967162417172>\n"
                "- Расслабиться."
            ),
            color=discord.Color(11927296),
        )

        admin_embed = discord.Embed(
            title="Зона администратора",
            color=discord.Color(6360573))
        admin_embed.add_field(
            name="Отношение к сообществу",
            value=answers["attitude"] or "Не указано")
        admin_embed.add_field(
            name="Указанный возраст",
            value=(f"{answers['age']}\n"), inline=True)
        admin_embed.add_field(
            name="Метод присоединения",
            value=(f"{source_names[source_key]}\n"))
        admin_embed.add_field(
            name="Ролевое мнение",
            value=(f"{answers['role_mind']}\n"),
            inline=False)

        await application_channel.send(
            embeds=[welcome_embed, admin_embed],
            view=ApplicationReviewView(),
        )
        await interaction.followup.send(
            f"Заявка создана: {application_channel.mention}",
            ephemeral=True,
        )


class VerificationView(discord.ui.View):
    def __init__(self) -> None:
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
            VerificationModal()
        )


class ClosureReasonModal(discord.ui.Modal, title="Причина"):
    reason = discord.ui.TextInput(
        label="Причина",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=950,
        custom_id="verification:closure_reason",
    )

    def __init__(self, status: str):
        """Создать форму причины закрытия заявки с указанным статусом."""
        super().__init__()
        self.status = status

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Обработать закрытие заявки модератором, отправить транскрипт и удалить канал."""
        if not _has_review_role(interaction):
            await interaction.response.send_message(
                "У вас нет доступа к обработке заявок.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "Эту заявку нельзя обработать в текущем канале.",
                ephemeral=True,
            )
            return

        applicant_id = _applicant_id(channel)
        if applicant_id is None:
            await interaction.followup.send(
                "Не удалось определить автора заявки.",
                ephemeral=True,
            )
            return

        applicant = channel.guild.get_member(applicant_id)
        log_channel = channel.guild.get_channel(int(os.environ['VERIFICATION_LOG_CHANNEL_ID']))
        if not isinstance(log_channel, discord.TextChannel):
            await interaction.followup.send(
                "Канал журнала верификации не найден. Заявка не закрыта.",
                ephemeral=True,
            )
            return

        if self.status == "ПРИНЯТО ✅":
            if applicant is None:
                await interaction.followup.send(
                    "Автор заявки больше не находится на сервере. Заявка не закрыта.",
                    ephemeral=True,
                )
                return

            verified_role = channel.guild.get_role(int(os.environ['VERIFIED_ROLE_ID']))
            if verified_role is None:
                await interaction.followup.send(
                    "Роль верификации для принятия заявки не найдена. Заявка не закрыта.",
                    ephemeral=True,
                )
                return

            try:
                await applicant.add_roles(
                    verified_role,
                    reason="Заявка на верификацию принята",
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "Бот не может изменить роли автора заявки. "
                    "Проверьте иерархию ролей.",
                    ephemeral=True,
                )
                return

        try:
            transcript = await _render_transcript(channel)
            await _lock_application(channel, applicant)
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as error:
            await interaction.followup.send(
                f"Не удалось закрыть доступ к заявке: {error}",
                ephemeral=True,
            )
            return

        reason = self.reason.value.strip()
        closure_embed = discord.Embed(
            title="Верификация закрыта",
            color=discord.Color(0),
        )
        closure_embed.add_field(
            name="Модератор:",
            value=interaction.user.mention,
            inline=False,
        )
        closure_embed.add_field(
            name="Автор заявки:",
            value=applicant.mention if applicant else f"<@{applicant_id}>",
            inline=False,
        )
        closure_embed.add_field(
            name="Статус заявки",
            value=f"{self.status}\n*Причина:*\n{reason}",
            inline=False,
        )

        try:
            await log_channel.send(
                embed=closure_embed,
                file=discord.File(
                    io.BytesIO(transcript.encode("utf-8")),
                    filename=f"transcript-{channel.id}.html",
                ),
            )
            await channel.delete(reason=f"Заявка закрыта: {self.status}")
        except (discord.Forbidden, discord.HTTPException) as error:
            await interaction.followup.send(
                f"Не удалось отправить итог или удалить канал заявки: {error}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Заявка обработана: {self.status}. Транскрипт отправлен в журнал.",
            ephemeral=True,
        )


class ApplicationReviewView(discord.ui.View):
    def __init__(self, *, question_disabled: bool = False):
        """Создать постоянную панель модерации с заданной доступностью кнопки вопроса."""
        super().__init__(timeout=None)
        self.question_button.disabled = question_disabled

    async def _deny_without_role(self, interaction: discord.Interaction) -> bool:
        """Сообщить об отсутствии роли модератора и вернуть True при отказе."""
        if _has_review_role(interaction):
            return False

        await interaction.response.send_message(
            "У вас нет доступа к обработке заявок.",
            ephemeral=True,
        )
        return True

    @discord.ui.button(
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="verification:accept",
    )
    async def accept_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Открыть модератору форму причины принятия заявки."""
        if await self._deny_without_role(interaction):
            return
        await interaction.response.send_modal(
            ClosureReasonModal(status="ПРИНЯТО ✅")
        )

    @discord.ui.button(
        style=discord.ButtonStyle.danger,
        emoji="✖",
        custom_id="verification:reject",
    )
    async def reject_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Открыть модератору форму причины отклонения заявки."""
        if await self._deny_without_role(interaction):
            return
        await interaction.response.send_modal(
            ClosureReasonModal(status="ОТКЛОНЕНО ❌")
        )

    @discord.ui.button(
        label="Задать вопрос",
        style=discord.ButtonStyle.primary,
        emoji="❔",
        custom_id="verification:ask_question",
    )
    async def question_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Открыть переписку с заявителем и отключить кнопку вопроса в сообщении."""
        if await self._deny_without_role(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Эту заявку нельзя открыть для переписки.",
                ephemeral=True,
            )
            return

        applicant_id = _applicant_id(channel)
        applicant = (
            channel.guild.get_member(applicant_id)
            if applicant_id is not None
            else None
        )
        if applicant is None:
            await interaction.response.send_message(
                "Автор заявки не найден на сервере.",
                ephemeral=True,
            )
            return

        try:
            await _set_conversation_access(channel, applicant, enabled=True)
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as error:
            await interaction.response.send_message(
                f"Не удалось открыть переписку: {error}",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            view=ApplicationReviewView(question_disabled=True)
        )
        await interaction.followup.send(
            "Переписка открыта для автора заявки и модераторов.",
            ephemeral=True,
        )


# Команда отладки для отправки embed-сообщения верификации
@app_commands.command(
    name="verification_embed",
    description="Отправить embed для подачи заявки на верификацию",
)
async def send_verification_embed(interaction: discord.Interaction):
    if interaction.user.id != int(os.environ['DEBUG_OWNER_ID']):
        await interaction.response.send_message(
            "Эта debug-команда доступна только владельцу проекта.",
            ephemeral=True,
        )
        return
    
    embed = discord.Embed(
    title="Точка верификации",
    description=("""
    Поздравляем!
    Вы были приглашены на этот закрытый сервер с проверенно-взрослыми фырками разных вкусов и характеров.
    Для последующего общения на сервере, вам, как и полагается, необходимо пройти простой опросник.
    Опросник читают живые люди, администраторы, потому время принятия может розниться.
    В случае если к вам возникнут вопросы - вас вызовут.
    
    Приятного времяпровождения!"""),
    color=13401344)
    embed.set_author(
    name="Horny Club",
    icon_url="https://cdn.discordapp.com/icons/1083623196164632616/12e253dcd6b354a20218193c7849ebff.webp?size=1024")
    embed.set_thumbnail(
    url="https://i.pinimg.com/736x/22/05/b8/2205b85e6ee1a84401729f80115ca54e.jpg")
    embed.set_footer(
    text="- .Alazell.              Если есть идеи - подавайте их мне.",
    icon_url="https://cdn.discordapp.com/avatars/626052608074711040/a4a7338b3faf52101ebfdb7d57360e92.webp?size=1024")
    # Отправляем сообщение
    await interaction.response.send_message(
        embed=embed,
        view=VerificationView(),
        ephemeral=False
    )
