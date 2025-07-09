import os
import logging
import discord
import sqlite3
from datetime import datetime, timedelta
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

# ===== SETUP =====
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    logging.error("Token tidak ditemukan! Pastikan file .env sudah berisi DISCORD_TOKEN.")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ===== DATABASE SETUP =====
DB_NAME = "ro_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Tabel untuk Share Loot
    c.execute('''CREATE TABLE IF NOT EXISTS shares (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        drops TEXT NOT NULL,
        channel_id INTEGER,
        creator_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'active'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS share_participants (
        share_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        FOREIGN KEY (share_id) REFERENCES shares (id)
    )''')
    
    # Tabel untuk Summer Race
    c.execute('''CREATE TABLE IF NOT EXISTS npcs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        theme TEXT,
        map_location TEXT NOT NULL,
        direction TEXT,
        map_link TEXT,
        map_image TEXT,
        npc_image TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS race_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        npc_id INTEGER NOT NULL,
        cooldown_timer INTEGER DEFAULT 30,
        navigation_command TEXT,
        pic_link TEXT,
        start_time TIMESTAMP,
        end_time TIMESTAMP,
        FOREIGN KEY (npc_id) REFERENCES npcs (id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS race_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        next_race TIMESTAMP NOT NULL
    )''')
    
    conn.commit()
    conn.close()

init_db()

# ===== DATABASE HELPER FUNCTIONS =====
def db_execute(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    last_id = c.lastrowid
    conn.close()
    return last_id

def db_fetch(query, params=(), fetch_one=False):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchone() if fetch_one else c.fetchall()
    conn.close()
    return result

# ===== EVENT HANDLERS =====
@bot.event
async def on_ready():
    print(f'Bot berhasil login sebagai {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
        check_race_schedule.start()
    except Exception as e:
        print(e)

# ===== SHARE LOOT SYSTEM =====
@bot.tree.command(name="setshares", description="Set channel untuk info share loot")
@app_commands.describe(channel="Channel untuk share loot")
async def set_shares_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    # Simpan channel ID di database (implementasi disederhanakan)
    await interaction.response.send_message(
        f"Channel share loot diatur ke: {channel.mention}"
    )

@bot.tree.command(name="sharescreate", description="Buat share sheet baru")
@app_commands.describe(name="Nama share", drops="Item drops (pisahkan dengan koma)")
async def create_share(interaction: discord.Interaction, name: str, drops: str):
    user_id = interaction.user.id
    share_id = db_execute(
        "INSERT INTO shares (name, drops, creator_id) VALUES (?, ?, ?)",
        (name, drops, user_id)
    )
    
    # Tambahkan pembuat sebagai peserta pertama
    db_execute(
        "INSERT INTO share_participants (share_id, user_id) VALUES (?, ?)",
        (share_id, user_id)
    )
    
    embed = discord.Embed(
        title=f"Share Sheet #{share_id} Dibuat",
        description=f"**{name}**",
        color=discord.Color.green()
    )
    embed.add_field(name="Drops", value=drops, inline=False)
    embed.add_field(name="Pembuat", value=interaction.user.mention, inline=True)
    embed.add_field(name="Status", value="🟢 Active", inline=True)
    embed.set_footer(text=f"Gunakan /shares {share_id} untuk melihat detail")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="shares", description="Lihat info share sheet")
@app_commands.describe(share_id="ID share sheet")
async def view_share(interaction: discord.Interaction, share_id: int):
    share = db_fetch(
        "SELECT * FROM shares WHERE id = ?", 
        (share_id,), 
        fetch_one=True
    )
    
    if not share:
        await interaction.response.send_message(f"Share sheet #{share_id} tidak ditemukan!")
        return
    
    participants = db_fetch(
        "SELECT user_id FROM share_participants WHERE share_id = ?",
        (share_id,)
    )
    
    # Format daftar peserta
    participants_list = []
    for user_id in participants:
        user = await bot.fetch_user(user_id[0])
        participants_list.append(user.mention)
    
    embed = discord.Embed(
        title=f"Share Sheet #{share_id}",
        description=f"**{share[1]}**",
        color=discord.Color.blue()
    )
    embed.add_field(name="Drops", value=share[2], inline=False)
    embed.add_field(name="Pembuat", value=f"<@{share[4]}>", inline=True)
    embed.add_field(name="Status", value=share[6], inline=True)
    embed.add_field(name="Peserta", value="\n".join(participants_list) if participants_list else "Belum ada peserta", inline=False)
    embed.set_footer(text=f"Dibuat pada: {share[5]}")
    
    await interaction.response.send_message(embed=embed)

# ===== SUMMER RACE SYSTEM =====
@bot.tree.command(name="mainnpcedit", description="Edit atau tambah NPC utama")
@app_commands.describe(
    npc_name="Nama NPC",
    theme="Tema",
    map_location="Lokasi map",
    direction="Arah",
    map_link="Link map (opsional)",
    map_image="Link gambar map (opsional)",
    npc_image="Link gambar NPC (opsional)"
)
async def edit_main_npc(
    interaction: discord.Interaction, 
    npc_name: str,
    map_location: str,
    direction: str,
    theme: str = None,
    map_link: str = None,
    map_image: str = None,
    npc_image: str = None
):
    # Cek apakah NPC sudah ada
    existing = db_fetch(
        "SELECT id FROM npcs WHERE name = ?",
        (npc_name,),
        fetch_one=True
    )
    
    if existing:
        db_execute(
            """UPDATE npcs SET 
            theme = ?, 
            map_location = ?, 
            direction = ?, 
            map_link = ?, 
            map_image = ?, 
            npc_image = ?
            WHERE name = ?""",
            (theme, map_location, direction, map_link, map_image, npc_image, npc_name)
        )
        action = "diperbarui"
    else:
        db_execute(
            """INSERT INTO npcs (
            name, theme, map_location, direction, map_link, map_image, npc_image
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (npc_name, theme, map_location, direction, map_link, map_image, npc_image)
        )
        action = "ditambahkan"
    
    await interaction.response.send_message(
        f"NPC **{npc_name}** berhasil {action}!"
    )

@bot.tree.command(name="setrace", description="Atur waktu race berikutnya")
@app_commands.describe(hours="Jam (00-23)", minutes="Menit (00-59)")
async def set_race_time(interaction: discord.Interaction, hours: int, minutes: int):
    now = datetime.now()
    next_race = now.replace(
        hour=hours, 
        minute=minutes, 
        second=0, 
        microsecond=0
    )
    
    # Jika waktu sudah lewat hari ini, atur untuk besok
    if next_race < now:
        next_race += timedelta(days=1)
    
    # Simpan jadwal
    db_execute(
        "INSERT INTO race_schedule (next_race) VALUES (?)",
        (next_race.isoformat(),)
    )
    
    await interaction.response.send_message(
        f"Race berikutnya dijadwalkan pada: {next_race.strftime('%d/%m/%Y %H:%M')}"
    )

@tasks.loop(minutes=1)
async def check_race_schedule():
    now = datetime.now()
    next_race = db_fetch(
        "SELECT next_race FROM race_schedule ORDER BY id DESC LIMIT 1",
        fetch_one=True
    )
    
    if next_race:
        race_time = datetime.fromisoformat(next_race[0])
        if now >= race_time:
            # Kirim notifikasi
            channel = bot.get_channel(YOUR_NOTIFICATION_CHANNEL_ID)  # Ganti dengan channel ID
            if channel:
                await channel.send(
                    f"@everyone Summer Race sudah dimulai! Gunakan /current untuk melihat NPC saat ini"
                )
            
            # Atur race berikutnya untuk besok
            next_race = race_time + timedelta(days=1)
            db_execute(
                "INSERT INTO race_schedule (next_race) VALUES (?)",
                (next_race.isoformat(),)
            )

@bot.tree.command(name="current", description="Tampilkan status NPC saat ini")
async def current_npc(interaction: discord.Interaction):
    # Ambil NPC aktif (disederhanakan)
    npc = db_fetch(
        "SELECT * FROM npcs ORDER BY RANDOM() LIMIT 1",
        fetch_one=True
    )
    
    if npc:
        embed = discord.Embed(
            title=f"NPC Aktif: {npc[1]}",
            description=f"**Tema**: {npc[2] or 'Tidak ada tema'}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Lokasi", value=npc[3], inline=True)
        embed.add_field(name="Arah", value=npc[4], inline=True)
        
        if npc[5]:
            embed.add_field(name="Link Map", value=f"[Klik disini]({npc[5]})", inline=False)
        if npc[6]:
            embed.set_image(url=npc[6])
        if npc[7]:
            embed.set_thumbnail(url=npc[7])
            
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("Tidak ada NPC aktif saat ini!")

# ===== ITEM & MONSTER DATABASE (Contoh) =====
ITEM_DB = {
    "white spider limb": {
        "id": 6325,
        "amount_needed": 8,
        "drops_from": "Dolomedes",
        "drop_rate": "100%",
        "best_map": "dic_fild02 (El Dicaste > South > South)",
        "property": "Wind"
    }
}

MONSTER_DB = {
    "horong": {
        "map": "dew_dun02",
        "location": "Dungeons > Payon Dungeon > South East > Center > South West",
        "quantity": 30,
        "mission_amount": 50,
        "spawn_window": "Instantly"
    }
}

@bot.tree.command(name="listitems", description="Tampilkan semua item di database")
async def list_items(interaction: discord.Interaction):
    if not ITEM_DB:
        await interaction.response.send_message("Database item kosong!")
        return

    embed = discord.Embed(
        title="Daftar Item di Database",
        color=discord.Color.purple()
    )
    for name, data in ITEM_DB.items():
        desc = f"ID: {data['id']}\nJumlah: {data['amount_needed']}\nDrop: {data['drops_from']}\nRate: {data['drop_rate']}\nMap: {data['best_map']}\nProperty: {data['property']}"
        embed.add_field(name=name.title(), value=desc, inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="item", description="Cari informasi item")
@app_commands.describe(item_name="Nama item yang dicari")
async def item_search(interaction: discord.Interaction, item_name: str):
    item_name = item_name.lower()
    if item_name in ITEM_DB:
        item = ITEM_DB[item_name]
        # ... (implementasi sama seperti sebelumnya)
    else:
        await interaction.response.send_message(f"Item '{item_name}' tidak ditemukan!")

# ===== RUN BOT =====
if __name__ == "__main__":
    bot.run(TOKEN)