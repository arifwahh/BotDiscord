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
    logging.error("Token not found! Please make sure .env file contains DISCORD_TOKEN.")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ===== DATABASE SETUP =====
DB_NAME = "ro_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Table for Share Loot
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
    
    # Table for Summer Race
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
@bot.event
async def on_ready():
    print(f'Bot logged in as {bot.user}')
    try:
        await bot.load_extension("mvp_tracker")  # Load cog, ini otomatis memanggil setup
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
        check_race_schedule.start()
    except Exception as e:
        print(f"Error during on_ready: {e}")


# ===== SHARE LOOT SYSTEM =====
@bot.tree.command(name="setshares", description="Set channel for share loot info")
@app_commands.describe(channel="Channel for share loot")
async def set_shares_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    # Save channel ID in database (simplified implementation)
    await interaction.response.send_message(
        f"Share loot channel set to: {channel.mention}"
    )

@bot.tree.command(name="sharescreate", description="Create a share sheet")
@app_commands.describe(name="Share Name", drops="Drop Items (Separated by commas)")
async def create_share(interaction: discord.Interaction, name: str, drops: str):
    user_id = interaction.user.id
    share_id = db_execute(
        "INSERT INTO shares (name, drops, creator_id) VALUES (?, ?, ?)",
        (name, drops, user_id)
    )
    
    # Add creator as first participant
    db_execute(
        "INSERT INTO share_participants (share_id, user_id) VALUES (?, ?)",
        (share_id, user_id)
    )
    
    embed = discord.Embed(
        title=f"Share Sheet #{share_id} Created",
        description=f"**{name}**",
        color=discord.Color.green()
    )
    embed.add_field(name="Drops", value=drops, inline=False)
    embed.add_field(name="Creator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Status", value="🟢 Active", inline=True)
    embed.set_footer(text=f"Use /shares {share_id} to view details")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="shares", description="View share sheet info")
@app_commands.describe(share_id="Share sheet ID")
async def view_share(interaction: discord.Interaction, share_id: int):
    share = db_fetch(
        "SELECT * FROM shares WHERE id = ?", 
        (share_id,), 
        fetch_one=True
    )
    
    if not share:
        await interaction.response.send_message(f"Share sheet #{share_id} not found!")
        return
    
    participants = db_fetch(
        "SELECT user_id FROM share_participants WHERE share_id = ?",
        (share_id,)
    )
    
    # Format participants list
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
    embed.add_field(name="Creator", value=f"<@{share[4]}>", inline=True)
    embed.add_field(name="Status", value=share[6], inline=True)
    embed.add_field(name="Participants", value="\n".join(participants_list) if participants_list else "No participants yet", inline=False)
    embed.set_footer(text=f"Created at: {share[5]}")
    
    await interaction.response.send_message(embed=embed)

# ===== SUMMER RACE SYSTEM =====
@bot.tree.command(name="mainnpcedit", description="Edit or add main NPC")
@app_commands.describe(
    npc_name="NPC name",
    theme="Theme",
    map_location="Map location",
    direction="Direction",
    map_link="Map link (optional)",
    map_image="Map image link (optional)",
    npc_image="NPC image link (optional)"
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
    # Check if NPC exists
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
        action = "updated"
    else:
        db_execute(
            """INSERT INTO npcs (
            name, theme, map_location, direction, map_link, map_image, npc_image
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (npc_name, theme, map_location, direction, map_link, map_image, npc_image)
        )
        action = "added"
    
    await interaction.response.send_message(
        f"NPC **{npc_name}** successfully {action}!"
    )

@bot.tree.command(name="setrace", description="Set next race time")
@app_commands.describe(hours="Hour (00-23)", minutes="Minute (00-59)")
async def set_race_time(interaction: discord.Interaction, hours: int, minutes: int):
    now = datetime.now()
    next_race = now.replace(
        hour=hours, 
        minute=minutes, 
        second=0, 
        microsecond=0
    )
    
    # If time already passed today, set for tomorrow
    if next_race < now:
        next_race += timedelta(days=1)
    
    # Save schedule
    db_execute(
        "INSERT INTO race_schedule (next_race) VALUES (?)",
        (next_race.isoformat(),)
    )
    
    await interaction.response.send_message(
        f"Next race scheduled at: {next_race.strftime('%m/%d/%Y %H:%M')}"
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
            # Send notification
            channel = bot.get_channel(YOUR_NOTIFICATION_CHANNEL_ID)  # Replace with channel ID
            if channel:
                await channel.send(
                    f"@everyone Summer Race has started! Use /current to see the current NPC"
                )
            
            # Set next race for tomorrow
            next_race = race_time + timedelta(days=1)
            db_execute(
                "INSERT INTO race_schedule (next_race) VALUES (?)",
                (next_race.isoformat(),)
            )

@bot.tree.command(name="current", description="Show current NPC status")
async def current_npc(interaction: discord.Interaction):
    # Get active NPC (simplified)
    npc = db_fetch(
        "SELECT * FROM npcs ORDER BY RANDOM() LIMIT 1",
        fetch_one=True
    )
    
    if npc:
        embed = discord.Embed(
            title=f"Active NPC: {npc[1]}",
            description=f"**Theme**: {npc[2] or 'No theme'}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Location", value=npc[3], inline=True)
        embed.add_field(name="Direction", value=npc[4], inline=True)
        
        if npc[5]:
            embed.add_field(name="Map Link", value=f"[Click here]({npc[5]})", inline=False)
        if npc[6]:
            embed.set_image(url=npc[6])
        if npc[7]:
            embed.set_thumbnail(url=npc[7])
            
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("No active NPC at this time!")

# ===== ITEM & MONSTER DATABASE (Example) =====
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

@bot.tree.command(name="listitems", description="List all items in database")
async def list_items(interaction: discord.Interaction):
    if not ITEM_DB:
        await interaction.response.send_message("Item database is empty!")
        return

    embed = discord.Embed(
        title="Item Database List",
        color=discord.Color.purple()
    )
    for name, data in ITEM_DB.items():
        desc = f"ID: {data['id']}\nAmount: {data['amount_needed']}\nDrops from: {data['drops_from']}\nRate: {data['drop_rate']}\nBest map: {data['best_map']}\nProperty: {data['property']}"
        embed.add_field(name=name.title(), value=desc, inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="item", description="Search for item information")
@app_commands.describe(item_name="Item name to search")
async def item_search(interaction: discord.Interaction, item_name: str):
    item_name = item_name.lower()
    if item_name in ITEM_DB:
        item = ITEM_DB[item_name]
        # ... (implementation same as before)
    else:
        await interaction.response.send_message(f"Item '{item_name}' not found!")

# ===== RUN BOT =====
if __name__ == "__main__":
    bot.run(TOKEN)