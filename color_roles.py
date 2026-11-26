from __future__ import annotations

import datetime
import io
import logging
import os
import re
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import find_dotenv, set_key
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

DB_PATH = "color_roles.db"
ANCHOR_KEY = "__ColorRoleRegulator__"
ROLE_NAME_TEMPLATE = "!       #{hex}"
ROLE_NAME_RE = re.compile(r"^!\s*#([0-9A-Fa-f]{6})$")
SIMILARITY_THRESHOLD = 255
MAX_OPTIONS = 3
EMPTY_ROLE_GRACE = datetime.timedelta(minutes=5)
ROLE_LIMIT_RESERVE = 5


def parse_hex(text: str) -> int | None:
    value = text.strip().lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{3}", value):
        value = "".join(ch * 2 for ch in value)
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return None
    rgb = int(value, 16)
    return rgb or 0x010101


def to_hex(rgb: int) -> str:
    return f"{rgb:06X}"


def role_name(rgb: int) -> str:
    return ROLE_NAME_TEMPLATE.format(hex=to_hex(rgb))


def rgb_from_name(name: str) -> int | None:
    match = ROLE_NAME_RE.match(name)
    return int(match.group(1), 16) if match else None


def color_distance(a: int, b: int) -> int:
    return sum(abs(((a >> s) & 0xFF) - ((b >> s) & 0xFF)) for s in (16, 8, 0))


def find_similar(target: int, cached: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    found = [
        (color_distance(target, rgb), role_id, rgb)
        for role_id, rgb in cached
        if color_distance(target, rgb) <= SIMILARITY_THRESHOLD
    ]
    found.sort()
    return found


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _text_colour(rgb: int) -> tuple[int, int, int]:
    r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return (20, 20, 20) if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else (245, 245, 245)


def render_preview(target: int, options: list[tuple[int, int, int]]) -> bytes:
    width, height, pad = 720, 240, 16
    image = Image.new("RGB", (width, height), (43, 45, 49))
    draw = ImageDraw.Draw(image)
    big, small = _font(30), _font(20)

    def swatch(box: tuple[int, int, int, int], rgb: int, title: str, subtitle: str) -> None:
        draw.rounded_rectangle(box, radius=14, fill=f"#{to_hex(rgb)}")
        ink = _text_colour(rgb)
        draw.text((box[0] + 14, box[1] + 12), title, font=big, fill=ink)
        draw.text((box[0] + 14, box[3] - 34), subtitle, font=small, fill=ink)

    if not options:
        swatch((pad, pad, width - pad, height - pad), target, f"#{to_hex(target)}", "new")
    else:
        left_w = (width - pad * 3) // 2
        swatch((pad, pad, pad + left_w, height - pad), target, f"#{to_hex(target)}", "new")
        x0 = pad * 2 + left_w
        shown = options[:MAX_OPTIONS]
        row_h = (height - pad * (len(shown) + 1)) // len(shown)
        for i, (dist, _role_id, rgb) in enumerate(shown):
            y0 = pad + i * (row_h + pad)
            box = (x0, y0, width - pad, y0 + row_h)
            draw.rounded_rectangle(box, radius=12, fill=f"#{to_hex(rgb)}")
            draw.text((x0 + 12, y0 + 8), f"#{to_hex(rgb)}   diff {dist}", font=small,
                      fill=_text_colour(rgb))

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS color_roles (
                role_id  INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                rgb      INTEGER NOT NULL)""")
        await db.commit()


async def _cached(guild_id: int) -> list[tuple[int, int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT role_id, rgb FROM color_roles WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            return [(int(r), int(c)) for r, c in await cursor.fetchall()]


async def _remember(role: discord.Role, rgb: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO color_roles VALUES (?, ?, ?) "
            "ON CONFLICT(role_id) DO UPDATE SET rgb = excluded.rgb",
            (role.id, role.guild.id, rgb))
        await db.commit()


async def _forget(role_ids: list[int]) -> None:
    if not role_ids:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("DELETE FROM color_roles WHERE role_id = ?", [(i,) for i in role_ids])
        await db.commit()


def _save_anchor_id(role_id: int) -> None:
    os.environ[ANCHOR_KEY] = str(role_id)
    path = find_dotenv(usecwd=True) or str(Path(__file__).with_name(".env"))
    try:
        Path(path).touch(exist_ok=True)
        set_key(path, ANCHOR_KEY, str(role_id), quote_mode="never")
    except OSError:
        logger.exception("Не удалось записать %s в %s", ANCHOR_KEY, path)


def resolve_anchor(guild: discord.Guild) -> discord.Role | None:
    raw = os.getenv(ANCHOR_KEY, "").strip()
    if raw.isdigit():
        role = guild.get_role(int(raw))
        if role is not None:
            return role
    role = discord.utils.get(guild.roles, name=ANCHOR_KEY)
    if role is not None and raw != str(role.id):
        _save_anchor_id(role.id)
    return role


async def sync_guild(guild: discord.Guild) -> None:
    now = discord.utils.utcnow()
    actual: dict[int, discord.Role] = {}
    for role in guild.roles:
        rgb = rgb_from_name(role.name)
        if rgb is None or role.managed:
            continue
        if not role.members and now - role.created_at > EMPTY_ROLE_GRACE:
            try:
                await role.delete(reason="Цветная роль без участников")
                continue
            except discord.HTTPException:
                logger.exception("Не удалось удалить пустую цветную роль %s", role.id)
        actual[role.id] = role
        await _remember(role, rgb)

    stale = [role_id for role_id, _ in await _cached(guild.id) if role_id not in actual]
    await _forget(stale)


async def sync_all(client: discord.Client) -> None:
    for guild in client.guilds:
        if guild.unavailable or resolve_anchor(guild) is None:
            continue
        try:
            await sync_guild(guild)
        except Exception:
            logger.exception("Ошибка синхронизации цветных ролей на сервере %s", guild.id)


async def _place_under_anchor(guild: discord.Guild, role: discord.Role, anchor: discord.Role) -> None:
    roles = [r for r in await guild.fetch_roles() if not r.is_default()]
    roles.sort(key=lambda r: (r.position, -r.id))
    current = {r.id: r.position for r in roles}
    order = [r for r in roles if r.id != role.id]
    anchor_index = next(i for i, r in enumerate(order) if r.id == anchor.id)
    order.insert(anchor_index, role)
    changes = {
        r: index + 1 for index, r in enumerate(order)
        if current.get(r.id) != index + 1 and index < anchor_index + 1
    }
    if changes:
        await guild.edit_role_positions(changes, reason="Цветная роль под __ColorRoleRegulator__")

    fresh = {r.id: r.position for r in await guild.fetch_roles()}
    if fresh.get(role.id, 0) >= fresh.get(anchor.id, 0):
        raise RuntimeError("Роль создана, но не встала под маркер")


async def create_color_role(guild: discord.Guild, rgb: int, anchor: discord.Role) -> discord.Role:
    role = await guild.create_role(
        name=role_name(rgb),
        colour=discord.Colour(rgb),
        permissions=discord.Permissions.none(),
        reason="Role Creator: новый цвет",
    )
    try:
        await _place_under_anchor(guild, role, anchor)
    except Exception:
        try:
            await role.delete(reason="Role Creator: не удалось поставить роль под маркер")
        except discord.HTTPException:
            logger.exception("Не удалось удалить роль %s после ошибки позиционирования", role.id)
        raise
    await _remember(role, rgb)
    return role


async def give_color(member: discord.Member, role: discord.Role) -> None:
    old = [r for r in member.roles if r.id != role.id and rgb_from_name(r.name) is not None]
    if old:
        await member.remove_roles(*old, reason="Role Creator: смена цвета")
    if role not in member.roles:
        await member.add_roles(role, reason="Role Creator: выбор цвета")
    await _delete_if_empty(old, member.id)


async def _delete_if_empty(roles: list[discord.Role], left_member_id: int) -> None:
    gone: list[int] = []
    for role in roles:
        if any(m.id != left_member_id for m in role.members):
            continue
        try:
            await role.delete(reason="Цветная роль без участников")
            gone.append(role.id)
        except discord.NotFound:
            gone.append(role.id)
        except discord.HTTPException:
            logger.exception("Не удалось удалить пустую цветную роль %s", role.id)
    await _forget(gone)


class ColorChoiceView(discord.ui.View):
    def __init__(self, author: discord.Member, target: int, anchor: discord.Role,
                 options: list[tuple[int, discord.Role]]) -> None:
        super().__init__(timeout=120)
        self.author = author
        self.target = target
        self.anchor = anchor
        self.message: discord.Message | None = None

        for dist, role in options:
            button = discord.ui.Button(
                label=f"Взять #{to_hex(role.colour.value)} (разница {dist})",
                style=discord.ButtonStyle.secondary,
            )
            button.callback = self._take_existing(role)
            self.add_item(button)

        create = discord.ui.Button(label=f"Создать свою #{to_hex(target)}",
                                   style=discord.ButtonStyle.primary)
        create.callback = self._create_own
        self.add_item(create)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Это меню не для тебя :)", ephemeral=True)
            return False
        return True

    def _take_existing(self, role: discord.Role):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            fresh = interaction.guild.get_role(role.id) if interaction.guild else None
            if fresh is None:
                await self._finish(interaction, "Эта роль уже исчезла. Вызови команду ещё раз.")
                return
            try:
                await give_color(self.author, fresh)
            except discord.HTTPException as error:
                await self._finish(interaction, f"Не получилось выдать роль: {error}")
                return
            await self._finish(interaction, f"Готово! Тебе выдан цвет #{to_hex(fresh.colour.value)}.")
        return callback

    async def _create_own(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            role = await create_color_role(self.author.guild, self.target, self.anchor)
            await give_color(self.author, role)
        except (discord.HTTPException, RuntimeError) as error:
            await self._finish(interaction, f"Не получилось создать роль: {error}")
            return
        await self._finish(interaction, f"Готово! Создан и выдан цвет #{to_hex(self.target)}.")

    async def _finish(self, interaction: discord.Interaction, text: str) -> None:
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(content=text, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="Время выбора вышло.", view=self)
            except discord.HTTPException:
                pass


def _check_setup(guild: discord.Guild) -> tuple[discord.Role | None, str | None]:
    anchor = resolve_anchor(guild)
    me = guild.me
    if anchor is None:
        return None, f"На сервере нет роли `{ANCHOR_KEY}`. Попроси администрацию её создать."
    if not me.guild_permissions.manage_roles:
        return None, "У бота нет права «Управлять ролями»."
    if anchor >= me.top_role:
        return None, f"Роль бота должна стоять выше `{ANCHOR_KEY}` в списке ролей."
    return anchor, None


@commands.hybrid_command(name="цвет", description="Выбрать цвет ника по hex-коду, например #FF5DD6")
@app_commands.describe(code="Цвет в формате #RRGGBB")
@commands.guild_only()
async def color_command(ctx: commands.Context, code: str) -> None:
    await ctx.defer(ephemeral=True)
    member = ctx.author
    guild = ctx.guild

    target = parse_hex(code)
    if target is None:
        await ctx.send("Не понял цвет. Пример: `#FF5DD6`.", ephemeral=True)
        return
    anchor, error = _check_setup(guild)
    if error:
        await ctx.send(error, ephemeral=True)
        return

    cached = await _cached(guild.id)
    similar = find_similar(target, cached)
    options: list[tuple[int, discord.Role]] = []
    for dist, role_id, _rgb in similar:
        role = guild.get_role(role_id)
        if role is not None:
            options.append((dist, role))

    if options and options[0][0] == 0:
        try:
            await give_color(member, options[0][1])
        except discord.HTTPException as error:
            await ctx.send(f"Не получилось выдать роль: {error}", ephemeral=True)
            return
        await ctx.send(f"Готово! Тебе выдан цвет #{to_hex(target)}.", ephemeral=True)
        return

    preview = discord.File(io.BytesIO(render_preview(target, [
        (dist, role.id, role.colour.value) for dist, role in options])), filename="color.png")

    if options:
        embed = discord.Embed(
            title="Похожий цвет уже есть",
            description=(
                "Слева твой цвет, справа похожие роли, которые уже есть (diff — разница, "
                "чем меньше, тем ближе). Можно взять существующую роль или создать свою."),
            colour=discord.Colour(target))
        embed.set_image(url="attachment://color.png")
        view = ColorChoiceView(member, target, anchor, options[:MAX_OPTIONS])
        view.message = await ctx.send(embed=embed, file=preview, view=view, ephemeral=True)
        return

    if len(guild.roles) >= 250 - ROLE_LIMIT_RESERVE:
        await ctx.send("На сервере почти не осталось места для новых ролей.", ephemeral=True)
        return
    try:
        role = await create_color_role(guild, target, anchor)
        await give_color(member, role)
    except (discord.HTTPException, RuntimeError) as error:
        await ctx.send(f"Не получилось создать роль: {error}", ephemeral=True)
        return

    embed = discord.Embed(title=f"Готово! Твой цвет #{to_hex(target)}", colour=discord.Colour(target))
    embed.set_image(url="attachment://color.png")
    await ctx.send(embed=embed, file=preview, ephemeral=True)


@commands.hybrid_command(name="цвет-снять", description="Убрать свой цвет ника")
@commands.guild_only()
async def color_remove_command(ctx: commands.Context) -> None:
    await ctx.defer(ephemeral=True)
    member = ctx.author
    old = [r for r in member.roles if rgb_from_name(r.name) is not None]
    if not old:
        await ctx.send("У тебя и так нет цветной роли.", ephemeral=True)
        return
    try:
        await member.remove_roles(*old, reason="Role Creator: цвет снят")
    except discord.HTTPException as error:
        await ctx.send(f"Не получилось снять роль: {error}", ephemeral=True)
        return
    await _delete_if_empty(old, member.id)
    await ctx.send("Цвет снят.", ephemeral=True)
