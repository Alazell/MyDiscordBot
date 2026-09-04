import os

import discord


TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not configured. Add the Discord bot token to Replit Secrets."
    )

intents = discord.Intents.none()
client = discord.Client(intents=intents)


@client.event
async def on_ready() -> None:
    if client.user is None:
        return

    await client.change_presence(
        status=discord.Status.online,
        activity=discord.CustomActivity(name="Я существую"),
    )

    print(f"Discord-бот запущен как {client.user}")
    print('Статус профиля: "Я существую"')


if __name__ == "__main__":
    client.run(TOKEN)
