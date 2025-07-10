import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
PERMA_CHANNEL_ID = int(os.getenv('PERMA_CHANNEL_ID'))

# Bot setup
intents = discord.Intents.default()
intents.message_content = True  # Aktifkan akses pesan
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Bot {bot.user.name} siap digunakan!')

@bot.command()
async def cek_channel(ctx):
    """Cek akses bot ke channel permanen"""
    channel = bot.get_channel(PERMA_CHANNEL_ID)
    
    if channel:
        await ctx.send(f"✅ Bot bisa akses channel: {channel.mention}\n"
                      f"📌 ID Channel: `{channel.id}`")
        """Dapatkan ID channel saat ini"""
        await ctx.send(f"ID channel ini: `{ctx.channel.id}`")
    else:
        await ctx.send("❌ Channel tidak ditemukan! Periksa:\n"
                      "- Apakah ID channel benar?\n"
                      "- Apakah bot sudah diinvite ke server?\n"
                      "- Cek permission bot")

# Jalankan bot
if __name__ == "__main__":
    bot.run(TOKEN)