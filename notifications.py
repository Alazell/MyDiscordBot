import logging
import os
import random

import discord

logger = logging.getLogger(__name__)

BIRTHDAY_MESSAGES = (
    "🎉 Сегодня день рождения у {mention}! Поздравляем, пусть этот год будет самым тёплым и пушистым 💜",
    "🎂 {mention}, с днём рождения! Обнимашки от всего клуба 🫂",
    "🥳 Внимание, именинник: {mention}! Заваливаем поздравлениями 🎁",
)
VERIFIED_MESSAGES = (
    "🌸 Встречайте {mention}! Верификация пройдена, добро пожаловать в клуб 💜",
    "✨ {mention} теперь с нами! Проходи, располагайся 🫂",
    "🦊 У нас новенький: {mention}! Поздоровайтесь 👋",
)


def _main_chat(guild: discord.Guild) -> discord.abc.Messageable | None:
    raw_id = os.getenv("MAIN_CHAT_CHANNEL_ID", "").strip()
    if not raw_id:
        return None
    try:
        channel = guild.get_channel_or_thread(int(raw_id))
    except ValueError:
        logger.error("MAIN_CHAT_CHANNEL_ID содержит неверный ID: %r", raw_id)
        return None
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        logger.warning("Основной чат %s не найден на сервере %s", raw_id, guild.id)
        return None
    return channel


async def _announce(member: discord.Member, templates: tuple[str, ...]) -> None:
    channel = _main_chat(member.guild)
    if channel is None:
        return
    try:
        await channel.send(
            random.choice(templates).format(mention=member.mention),
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
    except discord.HTTPException:
        logger.exception("Не удалось отправить уведомление в основной чат для %s", member.id)


async def announce_birthday(member: discord.Member) -> None:
    await _announce(member, BIRTHDAY_MESSAGES)


async def announce_verified(member: discord.Member) -> None:
    await _announce(member, VERIFIED_MESSAGES)
