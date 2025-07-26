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

        # Add this to your init_db() function under tables
        'ping_users': '''CREATE TABLE IF NOT EXISTS ping_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            notify_threshold INTEGER DEFAULT 50,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# ===== PING COMMAND =====
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    """Check bot latency"""
    latency = round(bot.latency * 1000)  # in ms
    await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")

# ===== USER MANAGEMENT COMMANDS =====
@bot.tree.command(name="add_user", description="Add user to receive notifications")
@app_commands.default_permissions(administrator=True)  # Only server admins can use this
@app_commands.describe(
    user="User to add",
    notify_threshold="Discount threshold for notifications (50-60%)"
)
async def add_user(
    interaction: discord.Interaction, 
    user: discord.User,
    notify_threshold: int = 50
):
    """Add user to receive notifications"""
    if notify_threshold < 50 or notify_threshold > 60:
        await interaction.response.send_message("Threshold must be between 50-60%")
        return
    
    try:
        db_execute(
            "INSERT OR REPLACE INTO ping_users (user_id, username, notify_threshold) VALUES (?, ?, ?)",
            (user.id, user.name, notify_threshold)
        )
        await interaction.response.send_message(
            f"✅ User {user.mention} added with notification threshold {notify_threshold}%"
        )
    except Exception as e:
        logger.error(f"Error adding user: {e}")
        await interaction.response.send_message("Failed to add user!")

@bot.tree.command(name="edit_user", description="Edit user notification settings")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    user="User to edit",
    notify_threshold="New discount threshold (50-60%)"
)
async def edit_user(
    interaction: discord.Interaction, 
    user: discord.User,
    notify_threshold: int
):
    """Edit user notification settings"""
    if notify_threshold < 50 or notify_threshold > 60:
        await interaction.response.send_message("Threshold must be between 50-60%")
        return
    
    try:
        affected = db_execute(
            "UPDATE ping_users SET notify_threshold = ? WHERE user_id = ?",
            (notify_threshold, user.id)
        )
        
        if affected:
            await interaction.response.send_message(
                f"✅ {user.mention}'s notification threshold updated to {notify_threshold}%"
            )
        else:
            await interaction.response.send_message("User not found in database!")
    except Exception as e:
        logger.error(f"Error editing user: {e}")
        await interaction.response.send_message("Failed to edit user!")

@bot.tree.command(name="remove_user", description="Remove user from notifications")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user="User to remove")
async def remove_user(interaction: discord.Interaction, user: discord.User):
    """Remove user from notifications"""
    try:
        affected = db_execute(
            "DELETE FROM ping_users WHERE user_id = ?",
            (user.id,)
        )
        
        if affected:
            await interaction.response.send_message(f"✅ {user.mention} removed from notifications")
        else:
            await interaction.response.send_message("User not found in database!")
    except Exception as e:
        logger.error(f"Error removing user: {e}")
        await interaction.response.send_message("Failed to remove user!")

@bot.tree.command(name="list_users", description="List all users receiving notifications")
@app_commands.default_permissions(administrator=True)
async def list_users(interaction: discord.Interaction):
    """List all users receiving notifications"""
    try:
        users = db_fetch("SELECT user_id, username, notify_threshold FROM ping_users ORDER BY username")
        
        if not users:
            await interaction.response.send_message("No users in notification list!")
            return
            
        embed = discord.Embed(
            title="🔔 Notification Users",
            description="Users who receive discount notifications",
            color=discord.Color.blue()
        )
        
        for user_id, username, threshold in users:
            embed.add_field(
                name=f"@{username}",
                value=f"Threshold: {threshold}%\nUser ID: {user_id}",
                inline=True
            )
            
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        await interaction.response.send_message("Failed to list users!")

# ===== VENDOR SCRAPER COMMANDS =====
# ===== VENDOR SCRAPER COMMANDS =====
@tasks.loop(minutes=15)
async def auto_scrape_vendors():
    """Automatically scrape vendor data with robust error handling"""
    logger.info("Starting automatic vendor scrape...")
    
    if not TALON_USERNAME or not TALON_PASSWORD:
        logger.error("Talon Tales credentials not configured!")
        return

    try:
        scraper = TalonTalesScraper()
        
        # Fix 1: Correct method call - was scraper_login (should be scraper.login)
        if not scraper.login(TALON_USERNAME, TALON_PASSWORD):
            logger.error("Login to Talon Tales failed!")
            return

        vendor_data = scraper.scrape_vendors(max_pages=1)
        
        if not vendor_data:
            logger.warning("No vendor data found in auto-scrape!")
            return

        # Fix 2: Proper error handling for save_to_db
        try:
            if scraper.save_to_db(vendor_data):
                logger.info(f"Auto-saved {len(vendor_data)} vendor listings")
            else:
                logger.error("Failed to save vendor data to database!")
                return
        except Exception as db_error:
            logger.error(f"Database save error: {db_error}")
            return
            
        # Calculate price statistics for new bargains
        stats = {}
        try:
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT item_name, MIN(price), AVG(price) 
                    FROM vendors 
                    GROUP BY item_name
                ''')
                stats = {row[0]: {'min': row[1], 'avg': int(row[2])} for row in cursor.fetchall()}
        except sqlite3.Error as sql_error:
            logger.error(f"Database query error: {sql_error}")
            return
            
        # Find new bargains (price < 80% of average)
        bargains = []
        for vendor in vendor_data:
            try:
                item_stats = stats.get(vendor['item_name'], {})
                if item_stats.get('avg') and vendor['price'] <= (item_stats['avg'] * 0.8):
                    bargains.append({
                        **vendor,
                        'discount': int((1 - (vendor['price'] / item_stats['avg'])) * 100)
                    })
            except Exception as bargain_error:
                logger.error(f"Error processing bargain: {bargain_error}")
                continue
                
        # Only proceed if we have valid bargains
        if not bargains:
            logger.info("No qualifying bargains found this scan")
            return
            
        # Get users who should be notified
        try:
            users_to_notify = db_fetch(
                "SELECT user_id, username, notify_threshold FROM ping_users"
            )
        except Exception as user_error:
            logger.error(f"Error fetching users: {user_error}")
            return
            
        # Create item to user mapping
        item_user_map = {}
        for user_id, username, threshold in users_to_notify:
            try:
                for bargain in bargains:
                    if bargain['discount'] >= threshold:
                        if bargain['item_name'] not in item_user_map:
                            item_user_map[bargain['item_name']] = []
                        item_user_map[bargain['item_name']].append(user_id)
            except Exception as mapping_error:
                logger.error(f"Error mapping users to items: {mapping_error}")
                continue
                
        # Send DM notifications
        for user_id, username, threshold in users_to_notify:
            try:
                user_bargains = [b for b in bargains if b['discount'] >= threshold]
                if user_bargains and (user := await bot.fetch_user(user_id)):
                    embed = discord.Embed(
                        title="🛍️ New Bargains Just For You!",
                        description="Items matching your notification threshold:",
                        color=discord.Color.green()
                    )
                    
                    for bargain in sorted(user_bargains, key=lambda x: x['discount'], reverse=True)[:3]:
                        embed.add_field(
                            name=f"【{bargain['item_name']}】 {bargain['discount']}% OFF",
                            value=(
                                f"Price: **{bargain['price']:,}z** (Avg: {stats[bargain['item_name']]['avg']:,}z)\n"
                                f"Vendor: `{bargain['vendor_name']}` at `{bargain['location']}`\n"
                                f"Use `/sj {bargain['vendor_name']}` to locate"
                            ),
                            inline=False
                        )
                    
                    await user.send(embed=embed)
            except Exception as dm_error:
                logger.error(f"Failed to notify user {user_id}: {dm_error}")
                continue
                
        # Send server notification if channel is configured
        # In your auto_scrape_vendors function, modify the server notification part:
        if hasattr(bot, 'scrape_notification_channel'):
            try:
                if channel := bot.get_channel(bot.scrape_notification_channel):
                    top_bargains = sorted(bargains, key=lambda x: x['discount'], reverse=True)[:3]
                    
                    embed = discord.Embed(
                        title="🛍️ New Bargains Found!",
                        description="Here are the best deals currently available:",
                        color=discord.Color.green()
                    )
                    
                    for bargain in top_bargains:
                        # Get unique interested users
                        interested_users = list(set(item_user_map.get(bargain['item_name'], [])))
                        
                        # Format mentions (show first 5 users + "and X more" if needed)
                        mention_text = ""
                        if interested_users:
                            max_shown = 5
                            if len(interested_users) > max_shown:
                                shown_users = interested_users[:max_shown]
                                mention_text = f"{' '.join([f'<@{user}>' for user in shown_users])} and {len(interested_users)-max_shown} more"
                            else:
                                mention_text = " ".join([f"<@{user}>" for user in interested_users])
                        else:
                            mention_text = "None"
                        
                        embed.add_field(
                            name=f"【{bargain['item_name']}】 {bargain['discount']}% OFF",
                            value=(
                                f"Price: **{bargain['price']:,}z** (Avg: {stats[bargain['item_name']]['avg']:,}z)\n"
                                f"Vendor: `{bargain['vendor_name']}` at `{bargain['location']}`\n"
                                f"Interested: {mention_text}\n"
                                f"Use `/sj {bargain['vendor_name']}` to locate"
                            ),
                            inline=False
                        )
                    
                    await channel.send(embed=embed)
            except Exception as channel_error:
                logger.error(f"Failed to send channel notification: {channel_error}")
    except Exception as e:
        logger.error(f"Auto-scrape failed: {str(e)}")
        # Optionally send error to a specific channel
        if hasattr(bot, 'error_notification_channel'):
            try:
                if channel := bot.get_channel(bot.error_notification_channel):
                    await channel.send(f"⚠️ Auto-scrape failed: {str(e)}")
            except:
                pass

# SEND NOTIFICATION ON SERVER COMMAND (Configure Server)
@bot.tree.command(name="set_notification_channel", description="Set the channel for bargain notifications")
@app_commands.default_permissions(administrator=True)  # Only server admins can use this
async def set_notification_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the channel for bargain notifications"""
    bot.scrape_notification_channel = channel.id
    await interaction.response.send_message(
        f"✅ Bargain notifications will now be sent to {channel.mention}",
        ephemeral=True
    )

# MANUALLY TRIGGER BARGAIN
@bot.tree.command(name="check_bargains", description="Manually check for bargains")
@app_commands.describe(threshold="Discount threshold (default: 50)")
async def check_bargains(interaction: discord.Interaction, threshold: int = 50):
    """Manually check for bargains"""
    # Defer the response to prevent interaction timeout
    await interaction.response.defer(thinking=True)
    
    if threshold < 0 or threshold > 100:
        await interaction.followup.send("Threshold must be between 0-100%")
        return
    
    try:
        # Calculate price statistics
        stats = {}
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT item_name, MIN(price), AVG(price) 
                FROM vendors 
                GROUP BY item_name
            ''')
            stats = {row[0]: {'min': row[1], 'avg': int(row[2])} for row in cursor.fetchall()}
        
        # Find current bargains
        bargains = []
        vendors = db_fetch('''
            SELECT item_name, MIN(price) as price, 
                   (SELECT vendor_name FROM vendors v2 
                    WHERE v2.item_name = v.item_name 
                    ORDER BY price ASC LIMIT 1) as vendor_name,
                   (SELECT location FROM vendors v2 
                    WHERE v2.item_name = v.item_name 
                    ORDER BY price ASC LIMIT 1) as location
            FROM vendors v
            GROUP BY item_name
        ''')
        
        for item in vendors:
            item_name, price, vendor_name, location = item
            if stats.get(item_name, {}).get('avg'):
                discount = int((1 - (price / stats[item_name]['avg'])) * 100)
                if discount >= threshold:
                    bargains.append({
                        'item_name': item_name,
                        'price': price,
                        'vendor_name': vendor_name,
                        'location': location,
                        'discount': discount,
                        'avg_price': stats[item_name]['avg']
                    })
        
        if not bargains:
            await interaction.followup.send(
                f"No bargains found with {threshold}% or higher discount!"
            )
            return
            
        embed = discord.Embed(
            title=f"🔥 Current Bargains ({threshold}%+ Discount)",
            description=f"Found {len(bargains)} items on sale!",
            color=discord.Color.orange()
        )
        
        for bargain in sorted(bargains, key=lambda x: x['discount'], reverse=True)[:5]:
            embed.add_field(
                name=f"【{bargain['item_name']}】 {bargain['discount']}% OFF",
                value=(
                    f"Price: **{bargain['price']:,}z** (Avg: {bargain['avg_price']:,}z)\n"
                    f"Vendor: `{bargain['vendor_name']}` at `{bargain['location']}`\n"
                    f"Use `/sj {bargain['vendor_name']}` to locate"
                ),
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Check bargains error: {e}")
        await interaction.followup.send(
            "Failed to check for bargains!",
            ephemeral=True
        )

@bot.tree.command(name="vendors", description="View vendor listings in classic format")
@app_commands.describe(
    search_term="Filter by item name",
    min_discount="Minimum discount percentage",
    limit="Number of results (max 10)"
)
async def view_vendors_classic(
    interaction: discord.Interaction,
    search_term: Optional[str] = None,
    min_discount: Optional[int] = None,
    limit: Optional[int] = 5
):
    """Display vendor listings in classic RO format"""
    try:
        limit = min(max(1, limit), 10)
        
        # Base query with price statistics
        query = """
            SELECT 
                v.item_name,
                v.price,
                v.vendor_name,
                v.location,
                stats.min_price,
                stats.avg_price
            FROM vendors v
            JOIN (
                SELECT 
                    item_name,
                    MIN(price) as min_price,
                    AVG(price) as avg_price
                FROM vendors
                GROUP BY item_name
            ) stats ON v.item_name = stats.item_name
        """
        params = []
        
        conditions = []
        if search_term:
            conditions.append("v.item_name LIKE ?")
            params.append(f"%{search_term}%")
        
        if min_discount:
            conditions.append("(1 - (v.price * 1.0 / stats.avg_price)) * 100 >= ?")
            params.append(min_discount)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY (stats.avg_price - v.price) DESC LIMIT ?"
        params.append(limit)
        
        vendors = db_fetch(query, params)
        
        if not vendors:
            await interaction.response.send_message("No vendors found matching your criteria!")
            return
        
        # Format message like reference image
        message = [
            "# shopee >",
            f"{len(vendors)} Online",
            "",
            "The BARGAIN item(s) in this list is currently available for purchase",
            "",
            f"Server Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "---"
        ]
        
        for vendor in vendors:
            item_name, price, vendor_name, location, min_price, avg_price = vendor
            discount = int((1 - (price / avg_price)) * 100) if avg_price else 0
            item_id = hash(item_name) % 10000  # Simulate item ID
            
            message.extend([
                f"@{vendor_name} <= {discount}% discount 【{item_name}】",
                f"[1]:{item_id} | **{price:,} z** (min) | {avg_price:,} z (avg) |",
                f"1 pcs | {location} ► absolute garbage, pls take it",
                ""
            ])
        
        await interaction.response.send_message(f"```\n{'\\n'.join(message)}\n```")
        
    except Exception as e:
        logger.error(f"Vendors command error: {e}")
        await interaction.response.send_message(
            "An error occurred while fetching vendor data",
            ephemeral=True
        )

@bot.tree.command(name="vendors_embed", description="View vendor listings with rich formatting")
@app_commands.describe(
    search_term="Filter by item name",
    min_discount="Minimum discount percentage",
    limit="Number of results (max 5)"
)
async def view_vendors_embed(
    interaction: discord.Interaction,
    search_term: Optional[str] = None,
    min_discount: Optional[int] = None,
    limit: Optional[int] = 3
):
    """Display vendor listings with rich embed formatting"""
    try:
        limit = min(max(1, limit), 5)
        
        query = """
            SELECT 
                item_name,
                MIN(price) as min_price,
                MAX(price) as max_price,
                AVG(price) as avg_price,
                COUNT(*) as vendor_count,
                (
                    SELECT location 
                    FROM vendors 
                    WHERE item_name = v.item_name 
                    ORDER BY price ASC 
                    LIMIT 1
                ) as cheapest_location,
                (
                    SELECT vendor_name 
                    FROM vendors 
                    WHERE item_name = v.item_name 
                    ORDER BY price ASC 
                    LIMIT 1
                ) as cheapest_vendor
            FROM vendors v
            GROUP BY item_name
        """
        params = []
        
        conditions = []
        if search_term:
            conditions.append("item_name LIKE ?")
            params.append(f"%{search_term}%")
        
        if min_discount:
            # Calculate discount based on average price
            conditions.append("(1 - (MIN(price) * 1.0 / AVG(price))) * 100 >= ?")
            params.append(min_discount)
        
        if conditions:
            query = f"WITH item_stats AS ({query}) SELECT * FROM item_stats WHERE " + " AND ".join(conditions)
        else:
            query = f"WITH item_stats AS ({query}) SELECT * FROM item_stats"
        
        query += " ORDER BY (avg_price - min_price) DESC LIMIT ?"
        params.append(limit)
        
        items = db_fetch(query, params)
        
        if not items:
            await interaction.response.send_message("No items found matching your criteria!")
            return
            
        embed = discord.Embed(
            title="🛍️ Shopee > Best Deals",
            description=f"Showing best prices for {len(items)} unique items",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Server Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        for item in items:
            item_name, min_price, max_price, avg_price, vendor_count, cheapest_location, cheapest_vendor = item
            discount = int((1 - (min_price / avg_price)) * 100) if avg_price else 0
            
            embed.add_field(
                name=f"【{item_name}】 {discount}% OFF",
                value=(
                    f"**Best Price:** {min_price:,}z (from `{cheapest_vendor}`)\n"
                    f"**Max Price:** {max_price:,}z\n"
                    f"**Average:** {int(avg_price):,}z\n"
                    f"**Available at {vendor_count} vendors**\n"
                    f"**Cheapest Location:** `{cheapest_location}`\n"
                    f"Use `/sj {cheapest_vendor}` to locate"
                ),
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        logger.error(f"Vendors embed command error: {e}")
        await interaction.response.send_message(
            "An error occurred while fetching vendor data",
            ephemeral=True
        )
        
@bot.tree.command(name="sj", description="Locate a specific vendor")
@app_commands.describe(vendor_name="Vendor name to locate")
async def locate_vendor(interaction: discord.Interaction, vendor_name: str):
    """Find a vendor's location and items"""
    try:
        vendors = db_fetch(
            """
            SELECT v.item_name, v.price, v.location, v.vendor_title, 
                   (SELECT AVG(price) FROM vendors WHERE item_name = v.item_name) as avg_price
            FROM vendors v
            WHERE v.vendor_name = ?
            ORDER BY v.price ASC
            """,
            (vendor_name,)
        )
        
        if not vendors:
            await interaction.response.send_message(f"No vendor found with name: {vendor_name}")
            return
            
        # Calculate best discount
        best_discount = 0
        for vendor in vendors:
            item_name, price, location, vendor_title, avg_price = vendor
            if avg_price:
                discount = int((1 - (price / avg_price)) * 100)
                if discount > best_discount:
                    best_discount = discount
        
        embed = discord.Embed(
            title=f"📍 Vendor {vendor_name}",
            description=f"**{vendor_title}**" if vendors[0][3] else "No title available",
            color=discord.Color.gold()
        )
        
        if best_discount > 0:
            embed.add_field(
                name="Best Deal",
                value=f"Up to {best_discount}% discount available!",
                inline=False
            )
        
        for vendor in vendors[:5]:  # Show first 5 items
            item_name, price, location, _, avg_price = vendor
            discount = int((1 - (price / avg_price)) * 100) if avg_price else 0
            
            embed.add_field(
                name=f"【{item_name}】",
                value=(
                    f"Price: **{price:,}z**\n"
                    f"Discount: {discount}%\n"
                    f"Location: `{location}`"
                ),
                inline=True
            )
        
        embed.set_footer(text="Use the navi command to visit this vendor")
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        logger.error(f"Vendor locate error: {e}")
        await interaction.response.send_message(
            "An error occurred while locating vendor",
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