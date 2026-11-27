from __future__ import annotations

import asyncio
from collections import Counter
import datetime
import io
import logging
import colorsys
import os
import re
from pathlib import Path

import aiosqlite
import discord
from discord.ext import commands
from dotenv import find_dotenv, set_key
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

HEX_NAME = re.compile(r"^!\s*#([0-9A-Fa-f]{6})$")
DB_FILE = "color_roles.db"
MAX_ROLES = 250 - 5


_FONT_CANDIDATES = (
    "arial.ttf", "segoeui.ttf", "tahoma.ttf", "Verdana.ttf", "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def _pick_font(size: int) -> ImageFont.ImageFont | None:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return None


HAS_CYRILLIC_FONT = _pick_font(24) is not None


def render_preview(target: int, options: list[tuple[tuple[int, int, int], int, int]]) -> bytes:
    def font(size: int) -> ImageFont.ImageFont:
        return _pick_font(size) or ImageFont.load_default()

    def ink(rgb: int) -> tuple[int, int, int]:
        r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
        return (20, 20, 20) if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else (245, 245, 245)

    if HAS_CYRILLIC_FONT:
        new_label, close_label = "новый", "близость {}%"
    else:
        new_label, close_label = "new", "diff {}%"

    width, height, pad = 720, 240, 16
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    big, small = font(30), font(20)

    def swatch(box, rgb, title, subtitle):
        draw.rounded_rectangle(box, radius=14, fill=f"#{rgb:06X}")
        colour = ink(rgb)
        draw.text((box[0] + 14, box[1] + 12), title, font=big, fill=colour)
        draw.text((box[0] + 14, box[3] - 34), subtitle, font=small, fill=colour)

    if not options:
        swatch((pad, pad, width - pad, height - pad), target, f"#{target:06X}", new_label)
    else:
        left_w = (width - pad * 3) // 2
        swatch((pad, pad, pad + left_w, height - pad), target, f"#{target:06X}", new_label)
        x0 = pad * 2 + left_w
        shown = options[:3]
        row_h = (height - pad * (len(shown) + 1)) // len(shown)
        for i, (diffs, _role_id, rgb) in enumerate(shown):
            y0 = pad + i * (row_h + pad)
            box = (x0, y0, width - pad, y0 + row_h)
            draw.rounded_rectangle(box, radius=12, fill=f"#{rgb:06X}")
            draw.text((x0 + 12, y0 + 8), f"#{rgb:06X}   {close_label.format(closeness(diffs))}",
                      font=small, fill=ink(rgb))


    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def init_db() -> None:
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS color_roles (
                role_id  INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                rgb      INTEGER NOT NULL)""")
        await db.commit()


async def _cached(guild_id: int) -> list[tuple[int, int]]:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT role_id, rgb FROM color_roles WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            return [(int(r), int(c)) for r, c in await cursor.fetchall()]


async def _store(remember: list[tuple[discord.Role, int]] | None = None,
                 forget: list[int] | None = None) -> None:
    async with aiosqlite.connect(DB_FILE) as db:
        if remember:
            await db.executemany(
                "INSERT INTO color_roles VALUES (?, ?, ?) "
                "ON CONFLICT(role_id) DO UPDATE SET rgb = excluded.rgb",
                [(r.id, r.guild.id, rgb) for r, rgb in remember])
        if forget:
            await db.executemany("DELETE FROM color_roles WHERE role_id = ?",
                                 [(i,) for i in forget])
        await db.commit()


def resolve_anchor(guild: discord.Guild) -> discord.Role | None:
    def save(role_id: int) -> None:
        os.environ['__ColorRoleRegulator__'] = str(role_id)
        path = find_dotenv(usecwd=True) or str(Path(__file__).with_name(".env"))
        try:
            Path(path).touch(exist_ok=True)
            set_key(path, '__ColorRoleRegulator__', str(role_id), quote_mode="never")
        except OSError:
            logger.exception("Не удалось записать %s в %s", '__ColorRoleRegulator__', path)

    raw = os.getenv('__ColorRoleRegulator__', "").strip()
    if raw.isdigit():
        role = guild.get_role(int(raw))
        if role is not None:
            return role
    role = discord.utils.get(guild.roles, name='__ColorRoleRegulator__')
    if role is not None and raw != str(role.id):
        save(role.id)
    return role


async def sync_all(client: discord.Client) -> None:
    for guild in client.guilds:
        if guild.unavailable or resolve_anchor(guild) is None:
            continue
        try:
            now = discord.utils.utcnow()
            cached_before = {role_id for role_id, _ in await _cached(guild.id)}
            remembered: list[tuple[discord.Role, int]] = []
            actual: set[int] = set()
            for role in guild.roles:
                match = HEX_NAME.match(role.name)
                rgb = int(match.group(1), 16) if match else None
                if rgb is None or role.managed:
                    continue
                if not role.members and now - role.created_at > datetime.timedelta(minutes=5):
                    try:
                        await role.delete(reason="Цветная роль без участников")
                        continue
                    except discord.HTTPException:
                        logger.exception("Не удалось удалить пустую цветную роль %s", role.id)
                actual.add(role.id)
                remembered.append((role, rgb))
            await _store(remember=remembered,
                         forget=[rid for rid in cached_before if rid not in actual])
        except Exception:
            logger.exception("Ошибка синхронизации цветных ролей на сервере %s", guild.id)


async def create_color_role(guild: discord.Guild, rgb: int, anchor: discord.Role) -> discord.Role:
    if len(guild.roles) >= MAX_ROLES:
        raise RuntimeError("На сервере почти не осталось места для новых ролей")
    role = await guild.create_role(
        name=f"!       #{rgb:06X}",
        colour=discord.Colour(rgb),
        permissions=discord.Permissions.none(),
        reason="Role Creator: новый цвет",
    )
    try:
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
    except Exception:
        try:
            await role.delete(reason="Role Creator: не удалось поставить роль под маркер")
        except discord.HTTPException:
            logger.exception("Не удалось удалить роль %s после ошибки позиционирования", role.id)
        raise
    await _store(remember=[(role, rgb)])
    return role


_member_locks: dict[tuple[int, int], asyncio.Lock] = {}


async def give_color(member: discord.Member, role: discord.Role) -> None:
    async with _member_locks.setdefault((member.guild.id, member.id), asyncio.Lock()):
        member = await member.guild.fetch_member(member.id)
        old = [r for r in member.roles if r.id != role.id and HEX_NAME.match(r.name)]
        had_role = role in member.roles
        if not had_role:
            await member.add_roles(role, reason="Role Creator: выбор цвета")
        if old:
            try:
                await member.remove_roles(*old, reason="Role Creator: смена цвета")
            except discord.HTTPException:
                if not had_role:
                    try:
                        await member.remove_roles(role, reason="Role Creator: откат смены цвета")
                        await drop_empty_roles([role], member.id)
                    except discord.HTTPException:
                        logger.exception("Не удалось откатить выдачу цвета %s", role.id)
                raise
        await drop_empty_roles(old, member.id)


async def drop_empty_roles(roles: list[discord.Role], left_member_id: int) -> None:
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
    await _store(forget=gone)


SIMILAR_CHANNEL = 60


def closeness(diffs: tuple[int, int, int]) -> int:
    return round((765 - sum(diffs)) / 765 * 100)


def find_similar(target, cached):
    def channel_diffs(a, b):
        return tuple(abs(((a >> s) & 0xFF) - ((b >> s) & 0xFF)) for s in (16, 8, 0))
    found = [(channel_diffs(target, rgb), rid, rgb) for rid, rgb in cached
             if max(channel_diffs(target, rgb)) <= SIMILAR_CHANNEL]
    found.sort(key=lambda item: (sum(item[0]), item[1]))
    return found


def hue_key(role):
    rgb = role.colour.value
    h, light, sat = colorsys.rgb_to_hls((rgb >> 16 & 0xFF) / 255,
                                        (rgb >> 8 & 0xFF) / 255, (rgb & 0xFF) / 255)
    return h, light, sat


def own_color(member):
    return next((r for r in member.roles if HEX_NAME.match(r.name)), None)


def panel_embed(own, *, headline=None, colour=None):
    if own is not None:
        title = headline or f"Твой цвет #{own.colour.value:06X}"
        description = "Ниже кнопки: можно выбрать другой цвет или снять текущий."
    else:
        title = headline or "У тебя пока нет цветовой роли"
        description = "Нажми «Выбрать цвет» и введи код вида #FF5DD6."
    return discord.Embed(title=(title or "—")[:250], description=description, colour=colour)


class ColorPanelView(discord.ui.View):
    def __init__(self, member, own):
        super().__init__(timeout=300)
        self.member = member
        self.message = None
        self.target = None
        self.anchor = None
        self.pages: list[str] = []
        self.index = 0
        self._build_panel_items(own)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Эта панель не для тебя :)", ephemeral=True)
            return False
        return True

    def _build_panel_items(self, own):
        self.clear_items()
        choose = discord.ui.Button(label="Выбрать цвет", style=discord.ButtonStyle.primary)
        choose.callback = self.on_choose
        self.add_item(choose)
        drop = discord.ui.Button(label="Удалить цвет", style=discord.ButtonStyle.danger,
                                 disabled=own is None)
        drop.callback = self.on_drop
        self.add_item(drop)
        browse = discord.ui.Button(label="Список 📋", style=discord.ButtonStyle.secondary)
        browse.callback = self.on_list
        self.add_item(browse)

    async def _edit(self, embed, file=None):
        payload: dict = {"embed": embed, "view": self, "attachments": []}
        if file is not None:
            embed.set_image(url="attachment://color.png")
            payload["attachments"] = [file]
        if self.message is not None:
            try:
                await self.message.edit(**payload)
            except discord.HTTPException:
                pass

    async def show_panel(self, own, *, headline=None, colour=None):
        self._build_panel_items(own)
        file = None
        if own is not None:
            file = discord.File(io.BytesIO(render_preview(own.colour.value, [])),
                                filename="color.png")
        await self._edit(panel_embed(own, headline=headline, colour=colour), file)

    async def on_choose(self, interaction):
        await interaction.response.send_modal(ColorPickModal(self))

    async def on_drop(self, interaction):
        await interaction.response.defer()
        async with _member_locks.setdefault((interaction.guild.id, self.member.id), asyncio.Lock()):
            member = await interaction.guild.fetch_member(self.member.id)
            old = [r for r in member.roles if HEX_NAME.match(r.name)]
            if not old:
                await self.show_panel(None, headline="У тебя и не было цветовой роли.",
                                      colour=discord.Colour.red())
                return
            try:
                await member.remove_roles(*old, reason="Role Creator: снятие цвета")
            except discord.HTTPException as error:
                await self.show_panel(own_color(member),
                                      headline=f"Не получилось снять роль: {error}",
                                      colour=discord.Colour.red())
                return
            await drop_empty_roles(old, member.id)
        await self.show_panel(None, headline="Готово! Цвет снят.",
                              colour=discord.Colour.green())

    def _list_embed(self):
        embed = discord.Embed(title="Цветовые роли сервера", description=self.pages[self.index])
        embed.set_footer(text=f"Страница {self.index + 1} из {len(self.pages)}")
        return embed

    def _jump(self, step):
        async def callback(interaction):
            if step == "first":
                self.index = 0
            elif step == "last":
                self.index = len(self.pages) - 1
            elif step == "prev":
                self.index = max(0, self.index - 1)
            else:
                self.index = min(len(self.pages) - 1, self.index + 1)
            await interaction.response.edit_message(embed=self._list_embed(), view=self)
        return callback

    async def on_list(self, interaction):
        roles = [r for r in interaction.guild.roles
                 if HEX_NAME.match(r.name) and not r.managed]
        if not roles:
            await interaction.response.edit_message(
                embed=panel_embed(own_color(self.member),
                                  headline="На сервере пока нет ни одной цветовой роли.",
                                  colour=discord.Colour.red()),
                attachments=[], view=self)
            return
        roles.sort(key=hue_key)
        colour_ids = {r.id for r in roles}
        counts = Counter(r.id for m in interaction.guild.members for r in m.roles if r.id in colour_ids)
        pages, lines, size = [], [], 0
        for role in roles:
            line = f"{counts[role.id]} 👤     {role.mention}"
            if lines and size + len(line) + 1 > 4000:
                pages.append("\n".join(lines))
                lines, size = [], 0
            lines.append(line)
            size += len(line) + 1
        if lines:
            pages.append("\n".join(lines))
        self.pages, self.index = pages, 0
        self.clear_items()
        if len(pages) > 1:
            for label, step in (("⏪", "first"), ("◀", "prev"), ("▶", "next"), ("⏩", "last")):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
                button.callback = self._jump(step)
                self.add_item(button)
        back = discord.ui.Button(label="Назад", style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.on_back
        self.add_item(back)
        await interaction.response.edit_message(embed=self._list_embed(), attachments=[], view=self)

    async def on_back(self, interaction):
        await interaction.response.defer()
        member = await interaction.guild.fetch_member(self.member.id)
        await self.show_panel(own_color(member))

    async def show_warning(self, member, target, anchor, options):
        self.target, self.anchor = target, anchor
        self.clear_items()
        for diffs, role in options:
            button = discord.ui.Button(
                label=f"Взять #{role.colour.value:06X} (близость {closeness(diffs)}%)",
                style=discord.ButtonStyle.primary)
            button.callback = self._apply(role)
            self.add_item(button)
        create = discord.ui.Button(label=f"Создать свою #{target:06X}",
                                   style=discord.ButtonStyle.secondary)
        create.callback = self._apply(None)
        self.add_item(create)
        embed = discord.Embed(
            title="Похожий цвет уже есть",
            description=(
                "Слева твой цвет, справа похожие роли, которые уже есть на сервере. "
                "Близость — насколько цвета похожи друг на друга: 100% — одинаковые, "
                "чем ниже процент, тем заметнее разница. "
                "Можно взять существующую роль или создать свою."),
            colour=discord.Colour.orange())
        file = discord.File(io.BytesIO(render_preview(
            target, [(diffs, role.id, role.colour.value) for diffs, role in options])),
            filename="color.png")
        await self._edit(embed, file)

    async def _fail_state(self, interaction, text):
        member = await interaction.guild.fetch_member(interaction.user.id)
        await self.show_panel(own_color(member), headline=text,
                              colour=discord.Colour.red())

    def _apply(self, role):
        async def callback(interaction):
            await interaction.response.defer()
            if role is None:
                try:
                    fresh = await create_color_role(interaction.guild, self.target, self.anchor)
                except (discord.HTTPException, RuntimeError) as error:
                    await self._fail_state(interaction, f"Не получилось создать роль: {error}")
                    return
            else:
                fresh = interaction.guild.get_role(role.id) if interaction.guild else None
                if fresh is None:
                    await self._fail_state(interaction, "Эта роль уже исчезла. Вызови команду ещё раз.")
                    return
            try:
                await give_color(interaction.user, fresh)
            except discord.HTTPException as error:
                await self._fail_state(interaction, f"Не получилось выдать роль: {error}")
                return
            done = "Создан и выдан" if role is None else "Тебе выдан"
            await self.show_panel(fresh,
                                  headline=f"Готово! {done} цвет #{fresh.colour.value:06X}.",
                                  colour=discord.Colour.green())
        return callback

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass


class ColorPickModal(discord.ui.Modal):
    code = discord.ui.TextInput(label="Введите значение...", placeholder="#FF5DD6",
                                min_length=1, max_length=7, required=True)

    def __init__(self, panel):
        super().__init__(title="Выбор цвета", timeout=300)
        self.panel = panel

    async def on_submit(self, interaction):
        await interaction.response.defer()
        try:
            await self._handle(interaction)
        except Exception:
            logger.exception("Ошибка выбора цвета")
            await self.panel.show_panel(own_color(interaction.user),
                                        headline="Что-то пошло не так. Попробуй ещё раз.",
                                        colour=discord.Colour.red())

    async def _handle(self, interaction):
        panel = self.panel
        value = str(self.code.value).strip().lstrip("#")
        if re.fullmatch(r"[0-9A-Fa-f]{3}", value):
            value = "".join(ch * 2 for ch in value)
        target = int(value, 16) if re.fullmatch(r"[0-9A-Fa-f]{6}", value) else None
        if target is None:
            await panel.show_panel(own_color(interaction.user),
                                   headline="Не понял цвет. Пример: `#FF5DD6`.",
                                   colour=discord.Colour.red())
            return
        target = target or 0x010101
        guild = interaction.guild
        anchor = resolve_anchor(guild)
        if anchor is None:
            await panel.show_panel(
                own_color(interaction.user),
                headline="На сервере нет роли `__ColorRoleRegulator__`. Попроси администрацию её создать.",
                colour=discord.Colour.red())
            return
        if not guild.me.guild_permissions.manage_roles:
            await panel.show_panel(own_color(interaction.user),
                                   headline="У бота нет права «Управлять ролями».",
                                   colour=discord.Colour.red())
            return
        if anchor >= guild.me.top_role:
            await panel.show_panel(
                own_color(interaction.user),
                headline="Роль бота должна стоять выше `__ColorRoleRegulator__` в списке ролей.",
                colour=discord.Colour.red())
            return
        member = interaction.user
        options = []
        for diffs, role_id, _rgb in find_similar(target, await _cached(guild.id)):
            role = guild.get_role(role_id)
            if role is not None:
                options.append((diffs, role))

        if options and not any(options[0][0]):
            try:
                await give_color(member, options[0][1])
            except discord.HTTPException as error:
                await self._fail(guild, f"Не получилось выдать роль: {error}")
                return
            await panel.show_panel(options[0][1],
                                   headline=f"Готово! Тебе выдан цвет #{target:06X}.",
                                   colour=discord.Colour.green())
            return

        if options:
            await panel.show_warning(member, target, anchor, options[:3])
            return

        if len(guild.roles) >= MAX_ROLES:
            await panel.show_panel(own_color(member),
                                   headline="На сервере почти не осталось места для новых ролей.",
                                   colour=discord.Colour.red())
            return
        try:
            role = await create_color_role(guild, target, anchor)
            await give_color(member, role)
        except (discord.HTTPException, RuntimeError) as error:
            await self._fail(guild, f"Не получилось создать роль: {error}")
            return
        await panel.show_panel(role,
                               headline=f"Готово! Создан и выдан цвет #{target:06X}.",
                               colour=discord.Colour.green())

    async def _fail(self, guild, text):
        member = await guild.fetch_member(self.panel.member.id)
        await self.panel.show_panel(own_color(member), headline=text,
                                    colour=discord.Colour.red())


@commands.hybrid_command(name="цвет", description="Панель управления цветом ника")
@commands.guild_only()
async def color_command(ctx: commands.Context) -> None:
    await ctx.defer(ephemeral=True)
    member = ctx.author
    own = own_color(member)
    view = ColorPanelView(member, own)
    embed = panel_embed(own)
    if own is not None:
        embed.set_image(url="attachment://color.png")
        view.message = await ctx.send(
            embed=embed, view=view,
            file=discord.File(io.BytesIO(render_preview(own.colour.value, [])), filename="color.png"),
            ephemeral=True)
    else:
        view.message = await ctx.send(embed=embed, view=view, ephemeral=True)
