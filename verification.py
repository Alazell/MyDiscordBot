import io
import json
import re
from html import escape
from pathlib import Path

import discord
from discord import app_commands


APPLICATION_CATEGORY_ID = os.getenv("APPLICATION_CATEGORY_ID")
MINOR_ROLE_ID = os.getenv("MINOR_ROLE_ID")
ADULT_ROLE_ID = os.getenv("ADULT_ROLE_ID")
REVIEW_ROLE_ID = os.getenv("REVIEW_ROLE_ID")
VERIFIED_ROLE_ID = os.getenv("VERIFIED_ROLE_ID")
PENDING_ROLE_ID = os.getenv("PENDING_ROLE_ID")
VERIFICATION_LOG_CHANNEL_ID = os.getenv("VERIFICATION_LOG_CHANNEL_ID")
DEBUG_OWNER_ID = os.getenv("DEBUG_OWNER_ID")
ANSWER_STORE = Path(__file__).with_name(".verification_answers.json")
APPLICATION_TOPIC_PREFIX = "verification_application:"
UNKNOWN_INVITE_VALUE = "Не определено"
INVITE_SNAPSHOTS: dict[int, dict[str, int]] = {}


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


async def refresh_invite_snapshot(guild: discord.Guild) -> None:
    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        return

    INVITE_SNAPSHOTS[guild.id] = {
        invite.code: invite.uses or 0
        for invite in invites
    }


async def record_member_invite(member: discord.Member) -> None:
    try:
        invites = await member.guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        return

    previous_uses = INVITE_SNAPSHOTS.get(member.guild.id, {})
    used_invites = [
        invite
        for invite in invites
        if (invite.uses or 0) > previous_uses.get(invite.code, 0)
    ]
    INVITE_SNAPSHOTS[member.guild.id] = {
        invite.code: invite.uses or 0
        for invite in invites
    }

    if not used_invites:
        return

    invite = max(
        used_invites,
        key=lambda item: (item.uses or 0) - previous_uses.get(item.code, 0),
    )
    inviter = invite.inviter.mention if invite.inviter else UNKNOWN_INVITE_VALUE
    expires_at = (
        invite.expires_at.strftime("%Y-%m-%d %H:%M UTC")
        if invite.expires_at
        else "не истекает"
    )
    details = {
        "invite_link": f"https://discord.gg/{invite.code}",
        "inviter": inviter,
        "invite_properties": (
            f"использований: {invite.uses or 0}\n"
            f"истекает: {expires_at}"
        ),
    }

    answers = _load_previous_answers()
    answers.setdefault(str(member.id), {}).update(details)
    try:
        _save_previous_answers(answers)
    except OSError:
        pass


def _channel_name(member: discord.Member) -> str:
    display_name = member.nick or member.name
    safe_name = re.sub(r"[^\w-]+", "-", display_name, flags=re.UNICODE)
    safe_name = safe_name.strip("-_").lower() or str(member.id)
    return f"заявка-{safe_name}"[:100]


def _applicant_id(channel: discord.TextChannel) -> int | None:
    topic = channel.topic or ""
    if not topic.startswith(APPLICATION_TOPIC_PREFIX):
        return None

    try:
        return int(topic.removeprefix(APPLICATION_TOPIC_PREFIX))
    except ValueError:
        return None


def _has_review_role(interaction: discord.Interaction) -> bool:
    return isinstance(interaction.user, discord.Member) and any(
        role.id == REVIEW_ROLE_ID for role in interaction.user.roles
    )


async def _set_conversation_access(
    channel: discord.TextChannel,
    applicant: discord.Member,
    *,
    enabled: bool,
) -> None:
    review_role = channel.guild.get_role(REVIEW_ROLE_ID)
    if review_role is None:
        raise RuntimeError(f"Роль модераторов не найдена: {REVIEW_ROLE_ID}")

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
    review_role = channel.guild.get_role(REVIEW_ROLE_ID)
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
        description="Называйте свой настоящий возраст. Плейсхолдер '14' это не подсказка.",
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
        text="Любимое пиво? :3",
        component=discord.ui.TextInput(
            placeholder="¯\\_(ツ)_/¯",
            custom_id="verification:beer",
            required=False,
            min_length=0,
            max_length=350,
        ),
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

        previous_answers = _load_previous_answers().get(str(user_id), {})
        for field, key in (
            (self.question_1.component, "attitude"),
            (self.question_2.component, "age"),
            (self.question_4.component, "beer"),
        ):
            if previous_answers.get(key):
                field.default = previous_answers[key]

        previous_source = previous_answers.get("source")
        if previous_source:
            for option in self.question_3.component.options:
                option.default = option.value == previous_source

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
        beer = self.question_4.component.value.strip()
        if not age_text.isdigit() or not 1 <= int(age_text) <= 120:
            await interaction.followup.send(
                "Укажите настоящий возраст двумя цифрами.",
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
        if source_key not in source_names:
            await interaction.followup.send(
                "Выберите способ присоединения к серверу.",
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

        age = int(age_text)
        age_role_id = MINOR_ROLE_ID if age < 18 else ADULT_ROLE_ID
        age_role = (
            interaction.guild.get_role(age_role_id)
            if age_role_id is not None
            else None
        )
        if age_role_id is not None and age_role is None:
            await interaction.followup.send(
                "Роль для указанного возраста не найдена. Сообщите об этом администрации.",
                ephemeral=True,
            )
            return

        if age_role is not None:
            try:
                await interaction.user.add_roles(
                    age_role,
                    reason="Возраст указан в заявке на верификацию",
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "Бот не может выдать возрастную роль. Проверьте права и иерархию ролей.",
                    ephemeral=True,
                )
                return

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
        }
        review_role = interaction.guild.get_role(REVIEW_ROLE_ID)
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
            "beer": beer,
            "invite_link": UNKNOWN_INVITE_VALUE,
            "inviter": UNKNOWN_INVITE_VALUE,
            "invite_properties": UNKNOWN_INVITE_VALUE,
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
                "*В остальных случаях - запрос примут без вопросов.*\n\n"
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
            value=(
                f"{source_names[source_key]}\n"
                "*подтверждение по ссылке:*\n"
                f"{answers['invite_link']}\n"
                "*приглашён участником:*\n"
                f"{answers['inviter']}\n"
                "*свойства ссылки-приглашения*\n"
                f"{answers['invite_properties']}"
            ),
            inline=False,
        )
        admin_embed.add_field(
            name="Возраст",
            value=(
                f"{answers['age']}\n"
                f"*Роль возраста: {age_role.mention if age_role else 'не назначается'}*"
            ),
            inline=True,
        )
        admin_embed.add_field(
            name="Любимое пиво",
            value=answers["beer"] or "Не указано",
            inline=True,
        )

        await application_channel.send(
            embeds=[welcome_embed, admin_embed],
            view=ApplicationReviewView(),
        )
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


class ClosureReasonModal(discord.ui.Modal, title="Причина"):
    reason = discord.ui.TextInput(
        label="Причина",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1024,
        custom_id="verification:closure_reason",
    )

    def __init__(self, status: str):
        super().__init__()
        self.status = status

    async def on_submit(self, interaction: discord.Interaction) -> None:
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
        log_channel = channel.guild.get_channel(VERIFICATION_LOG_CHANNEL_ID)
        if not isinstance(log_channel, discord.TextChannel):
            await interaction.followup.send(
                "Канал журнала верификации не найден. Заявка не закрыта.",
                ephemeral=True,
            )
            return

        if self.status == "Принято":
            if applicant is None:
                await interaction.followup.send(
                    "Автор заявки больше не находится на сервере. Заявка не закрыта.",
                    ephemeral=True,
                )
                return

            verified_role = channel.guild.get_role(VERIFIED_ROLE_ID)
            pending_role = channel.guild.get_role(PENDING_ROLE_ID)
            if verified_role is None or pending_role is None:
                await interaction.followup.send(
                    "Одна из ролей для принятия заявки не найдена. Заявка не закрыта.",
                    ephemeral=True,
                )
                return

            try:
                await applicant.add_roles(
                    verified_role,
                    reason="Заявка на верификацию принята",
                )
                await applicant.remove_roles(
                    pending_role,
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

        saved_answers = _load_previous_answers().get(str(applicant_id), {})
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
        closure_embed.add_field(
            name="Приглашён:",
            value=saved_answers.get("inviter", UNKNOWN_INVITE_VALUE),
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
        super().__init__(timeout=None)
        self.question_button.disabled = question_disabled

    async def _deny_without_role(self, interaction: discord.Interaction) -> bool:
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
        if await self._deny_without_role(interaction):
            return
        await interaction.response.send_modal(
            ClosureReasonModal(status="ПРИНЯТО ✅")
        )

    @discord.ui.button(
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="verification:reject",
    )
    async def reject_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
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
    if interaction.user.id != DEBUG_OWNER_ID:
        await interaction.response.send_message(
            "Эта debug-команда доступна только владельцу проекта.",
            ephemeral=True,
        )
        return

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

class DebugGroup(discord.app_commands.Group):
    """Отладочные команды проекта"""

    def __init__(self):
        super().__init__(name="debug", description="Отладочные команды проекта")
        # Добавляем новые команды сюда
        self.add_command(send_verification_embed)

