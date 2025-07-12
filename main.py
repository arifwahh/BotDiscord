import os
import logging
import discord
import sqlite3
from datetime import datetime, timedelta
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from typing import Optional, List, Dict
from talon_scraper import TalonTalesScraper

# ===== SETUP =====
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
TALON_USERNAME = os.getenv('TALON_USERNAME')
TALON_PASSWORD = os.getenv('TALON_PASSWORD')

if not TOKEN:
    logging.error("Discord token not found! Please check your .env file")
    exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Initialize scraper
scraper = TalonTalesScraper()

# ===== DATABASE SETUP =====
DB_NAME = "ro_bot.db"

def init_db():
    """Initialize database tables"""
    tables = {
        'shares': '''CREATE TABLE IF NOT EXISTS shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            drops TEXT NOT NULL,
            channel_id INTEGER,
            creator_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active'
        )''',
        
        'share_participants': '''CREATE TABLE IF NOT EXISTS share_participants (
            share_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            FOREIGN KEY (share_id) REFERENCES shares (id)
        )''',
        
        'npcs': '''CREATE TABLE IF NOT EXISTS npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            theme TEXT,
            map_location TEXT NOT NULL,
            direction TEXT,
            map_link TEXT,
            map_image TEXT,
            npc_image TEXT,
            coordinates TEXT,
            function TEXT,
            quests TEXT,
            shop_items TEXT,
            scraped_at TIMESTAMP
        )''',
        
        'race_events': '''CREATE TABLE IF NOT EXISTS race_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            npc_id INTEGER NOT NULL,
            cooldown_timer INTEGER DEFAULT 30,
            navigation_command TEXT,
            pic_link TEXT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            FOREIGN KEY (npc_id) REFERENCES npcs (id)
        )''',
        
        'race_schedule': '''CREATE TABLE IF NOT EXISTS race_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            next_race TIMESTAMP NOT NULL
        )''',
        
        'items': '''CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            amount_needed INTEGER DEFAULT 0,
            drops_from TEXT,
            drop_rate TEXT,
            best_map TEXT,
            property TEXT,
            type TEXT,
            weight INTEGER,
            attack TEXT,
            defense TEXT,
            item_range TEXT,
            slots TEXT,
            job_class TEXT,
            level_requirement TEXT,
            description TEXT,
            dropped_by TEXT,
            scraped_at TIMESTAMP
        )''',
        
        'vendors': '''CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT,
            item_name TEXT,
            price INTEGER,
            amount INTEGER,
            vendor_name TEXT,
            vendor_title TEXT,
            location TEXT,
            icon_url TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )'''
    }

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        for table_name, table_sql in tables.items():
            cursor.execute(table_sql)
        conn.commit()

init_db()

# ===== DATABASE HELPER FUNCTIONS =====
def db_execute(query: str, params=()) -> int:
    """Execute a write query and return lastrowid"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.lastrowid

def db_fetch(query: str, params=(), fetch_one: bool = False):
    """Execute a read query and return results"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone() if fetch_one else cursor.fetchall()

# ===== EVENT HANDLERS =====
@bot.event
async def on_ready():
    """Called when bot is ready"""
    logger.info(f'Bot logged in as {bot.user}')
    try:
        await bot.load_extension("mvp_tracker")
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} commands")
        check_race_schedule.start()
        auto_scrape_vendors.start()  # Start the auto-scrape task
    except Exception as e:
        logger.error(f"Error during on_ready: {e}")

@bot.tree.command(name="set_scrape_channel", description="Set channel for scrape notifications")
@app_commands.describe(channel="Channel for scrape notifications")
async def set_scrape_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the channel for scrape notifications"""
    bot.scrape_notification_channel = channel.id
    await interaction.response.send_message(
        f"Scrape notifications will be sent to {channel.mention}"
    )

# ===== SHARE LOOT SYSTEM =====
@bot.tree.command(name="setshares", description="Set channel for share loot info")
@app_commands.describe(channel="Channel for share loot")
async def set_shares_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the channel for share loot notifications"""
    try:
        share_id = db_execute(
            "UPDATE shares SET channel_id = ? WHERE id = (SELECT MAX(id) FROM shares)",
            (channel.id,)
        )
        await interaction.response.send_message(
            f"Share loot channel set to: {channel.mention} for share #{share_id}"
        )
    except Exception as e:
        logger.error(f"Error setting share channel: {e}")
        await interaction.response.send_message("Failed to set share channel!")

# ... [Keep all your existing share loot commands unchanged] ...
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

# ===== VENDOR SCRAPER COMMANDS =====
@tasks.loop(minutes=15)  # Increased interval to 15 minutes
async def auto_scrape_vendors():
    """Automatically scrape vendor data with robust error handling"""
    logger.info("Starting automatic vendor scrape...")
    
    if not TALON_USERNAME or not TALON_PASSWORD:
        logger.error("Talon Tales credentials not configured!")
        return

    try:
        # Create new scraper instance for each run
        scraper = TalonTalesScraper()
        
        if not scraper.login(TALON_USERNAME, TALON_PASSWORD):
            logger.error("Login to Talon Tales failed!")
            return

        # Only scrape first page for automatic updates
        vendor_data = scraper.scrape_vendors(max_pages=1)
        
        if not vendor_data:
            logger.warning("No vendor data found in auto-scrape!")
            return

        if scraper.save_to_db(vendor_data):
            logger.info(f"Auto-saved {len(vendor_data)} vendor listings")
            
            # Notification logic remains the same
            if hasattr(bot, 'scrape_notification_channel'):
                channel = bot.get_channel(bot.scrape_notification_channel)
                if channel:
                    embed = discord.Embed(
                        title="🔄 Automatic Vendor Update",
                        description=f"Scraped {len(vendor_data)} new vendor listings",
                        color=discord.Color.blurple()
                    )
                    await channel.send(embed=embed)
                    
    except Exception as e:
        logger.error(f"Auto-scrape failed: {str(e)}")
    finally:
        # Clean up
        if 'scraper' in locals():
            scraper.session.close()

@bot.tree.command(name="view_vendors", description="View scraped vendor data")
@app_commands.describe(
    search_term="Filter by item or vendor name",
    limit="Number of results to show (max 20)"
)
async def view_vendors(
    interaction: discord.Interaction,
    search_term: Optional[str] = None,
    limit: Optional[int] = 5
):
    """Display vendor listings from database"""
    try:
        limit = min(max(1, limit), 20)  # Ensure limit is between 1-20
        
        query = """SELECT item_name, price, amount, vendor_name, location 
                   FROM vendors"""
        params = []
        
        if search_term:
            query += " WHERE item_name LIKE ? OR vendor_name LIKE ?"
            params.extend([f"%{search_term}%", f"%{search_term}%"])
            
        query += " ORDER BY scraped_at DESC LIMIT ?"
        params.append(limit)
        
        vendors = db_fetch(query, params)
        
        if not vendors:
            await interaction.response.send_message("No vendors found matching your criteria!")
            return
            
        embed = discord.Embed(
            title=f"🛒 Vendor Listings ({len(vendors)} results)",
            color=discord.Color.blue()
        )
        
        for item in vendors:
            name, price, amount, vendor, location = item
            embed.add_field(
                name=f"{name} - {price:,}z",
                value=f"**Vendor**: {vendor}\n**Stock**: {amount}\n**Location**: {location}",
                inline=False
            )
            
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        logger.error(f"View vendors error: {e}")
        await interaction.response.send_message(
            "An error occurred while fetching vendor data",
            ephemeral=True
        )

# ... [Keep all your existing race system commands unchanged] ...

# ... [Keep all your existing item/monster database commands unchanged] ...
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
    # Get active NPC with combined data
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
        # Original fields
        embed.add_field(name="Location", value=npc[3], inline=True)
        embed.add_field(name="Direction", value=npc[4], inline=True)
        
        # New scraped fields if available
        if npc[8]:  # coordinates
            embed.add_field(name="Coordinates", value=npc[8], inline=True)
        if npc[9]:  # function
            embed.add_field(name="Function", value=npc[9], inline=False)
            
        # Original media fields
        if npc[5]:  # map_link
            embed.add_field(name="Map Link", value=f"[Click here]({npc[5]})", inline=False)
        if npc[6]:  # map_image
            embed.set_image(url=npc[6])
        if npc[7]:  # npc_image
            embed.set_thumbnail(url=npc[7])
            
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("No active NPC at this time!")

@bot.tree.command(name="dbdescribe", description="Describe columns and row count of a table")
@app_commands.describe(table="Table name")
async def describe_table(interaction: discord.Interaction, table: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        # Cek kolom-kolom tabel
        c.execute(f"PRAGMA table_info({table});")
        columns = c.fetchall()

        if not columns:
            await interaction.response.send_message(f"No columns found or table '{table}' doesn't exist.")
            conn.close()
            return

        # Hitung jumlah baris
        c.execute(f"SELECT COUNT(*) FROM {table};")
        row_count = c.fetchone()[0]

    except sqlite3.OperationalError as e:
        await interaction.response.send_message(f"Error: {e}")
        conn.close()
        return
    finally:
        conn.close()

    column_info = "\n".join([f"• {col[1]} ({col[2]})" for col in columns])

    embed = discord.Embed(
        title=f"🧾 Table: `{table}`",
        description=column_info,
        color=discord.Color.orange()
    )
    embed.set_footer(text=f"Total rows: {row_count}")

    await interaction.response.send_message(embed=embed)


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
    # Search in both ITEM_DB and database
    item_name_lower = item_name.lower()
    
    # First check your hardcoded ITEM_DB
    if item_name_lower in ITEM_DB:
        item_data = ITEM_DB[item_name_lower]
        embed = discord.Embed(
            title=f"{item_name.title()} (Manual Data)",
            color=discord.Color.blue()
        )
        # Add your existing fields
        embed.add_field(name="ID", value=item_data['id'], inline=True)
        embed.add_field(name="Amount Needed", value=item_data['amount_needed'], inline=True)
        embed.add_field(name="Drops From", value=item_data['drops_from'], inline=True)
        # ... add other fields from ITEM_DB ...
        
        await interaction.response.send_message(embed=embed)
        return
    
    # If not in ITEM_DB, search the database
    item = db_fetch(
        "SELECT * FROM items WHERE name LIKE ? LIMIT 1",
        (f"%{item_name}%",),
        fetch_one=True
    )
    
    if item:
        embed = discord.Embed(
            title=f"{item[1]} (ID: {item[0]})",
            description=item[15] or "No description available",  # description field
            color=discord.Color.green()
        )
        
        # Add fields from database
        fields = [
            ("Type", item[7], True),
            ("Weight", item[8], True),
            ("Attack", item[9], True),
            ("Dropped By", item[16] or "Unknown", False),
            ("Best Map", item[5] or "Unknown", True),  # from your ITEM_DB structure
            ("Property", item[6] or "Unknown", True)   # from your ITEM_DB structure
        ]
        
        for name, value, inline in fields:
            if value:  # Only add field if value exists
                embed.add_field(name=name, value=value, inline=inline)
                
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(f"Item '{item_name}' not found in database!")

# ===== DATABASE MANAGEMENT COMMANDS =====
@bot.tree.command(name="dblisttables", description="List all tables in the database")
async def list_tables(interaction: discord.Interaction):
    """List all database tables"""
    try:
        tables = db_fetch("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        if not tables:
            await interaction.response.send_message("No tables found in database.")
            return

        embed = discord.Embed(
            title="📋 Database Tables",
            description="\n".join(f"• {table[0]}" for table in tables),
            color=discord.Color.dark_teal()
        )
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Error listing tables: {e}")
        await interaction.response.send_message("Failed to list database tables!")

# ===== RUN BOT =====
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Bot startup failed: {e}")