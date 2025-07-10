# === mvp_tracker.py ===
import logging
import sqlite3
import pytz
from datetime import datetime, timedelta
from prettytable import PrettyTable
from contextlib import closing
import discord
from discord.ext import commands, tasks
from discord import app_commands

DB_NAME = "ro_bot.db"

class MVPTrackerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pst_tz = pytz.timezone('America/Los_Angeles')
        self.tracker_message = None
        self.time_message = None
        self.tracker_channel = None
        self._initialize_db()
        self.update_tracker.start()

    def cog_unload(self):
        self.update_tracker.cancel()

    def _initialize_db(self):
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS mvp_database (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        downtime INTEGER NOT NULL,
                        spawn_range INTEGER NOT NULL
                    )''')
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS tracked_mvps (
                        mvp_name TEXT PRIMARY KEY,
                        tracking_since TEXT NOT NULL,
                        last_death TEXT,
                        next_spawn_start TEXT,
                        next_spawn_end TEXT,
                        FOREIGN KEY (mvp_name) REFERENCES mvp_database(name)
                    )''')

    def _execute_db(self, query: str, params: tuple = (), fetch: bool = False):
        try:
            with closing(sqlite3.connect(DB_NAME)) as conn:
                with conn:
                    cursor = conn.cursor()
                    cursor.execute(query, params)
                    if fetch:
                        return cursor.fetchall()
                    return cursor.lastrowid
        except sqlite3.Error as e:
            logging.error(f"Database error: {e}")
            return None

    def add_mvp(self, mvp_name: str, downtime: int, spawn_range: int) -> str:
        mvp_name = mvp_name.lower()
        self._execute_db(
            "INSERT OR REPLACE INTO mvp_database (name, downtime, spawn_range) VALUES (?, ?, ?)",
            (mvp_name, downtime, spawn_range)
        )
        return f"✅ Added/Updated {mvp_name} (DT: {downtime}m, SR: {spawn_range}m)"

    def track(self, mvp_name: str, time_of_death: str = None) -> str:
        mvp_name = mvp_name.lower()
        if not self._execute_db("SELECT 1 FROM mvp_database WHERE name = ?", (mvp_name,), fetch=True):
            return f"❌ {mvp_name} not found in database"

        now = datetime.now(self.pst_tz)
        if time_of_death:
            try:
                hours, minutes = map(int, time_of_death.split(':'))
                death_time = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
                if death_time > now:
                    death_time -= timedelta(days=1)
            except ValueError:
                return "❌ Invalid time format. Use HH:MM (24-hour format)"
        else:
            death_time = now

        downtime, spawn_range = self._execute_db(
            "SELECT downtime, spawn_range FROM mvp_database WHERE name = ?",
            (mvp_name,), fetch=True
        )[0]
        next_spawn_start = death_time + timedelta(minutes=downtime)
        next_spawn_end = next_spawn_start + timedelta(minutes=spawn_range)

        self._execute_db(
            '''INSERT OR REPLACE INTO tracked_mvps
            (mvp_name, tracking_since, last_death, next_spawn_start, next_spawn_end)
            VALUES (?, ?, ?, ?, ?)''',
            (mvp_name, now.isoformat(), death_time.isoformat(), next_spawn_start.isoformat(), next_spawn_end.isoformat())
        )
        return f"✅ Now tracking {mvp_name}"

    def remove(self, mvp_name: str) -> str:
        self._execute_db("DELETE FROM tracked_mvps WHERE mvp_name = ?", (mvp_name.lower(),))
        return f"✅ Removed {mvp_name} from tracker"

    def delete_mvp(self, mvp_name: str) -> str:
        with closing(sqlite3.connect(DB_NAME)) as conn:
            with conn:
                conn.execute("DELETE FROM tracked_mvps WHERE mvp_name = ?", (mvp_name.lower(),))
                conn.execute("DELETE FROM mvp_database WHERE name = ?", (mvp_name.lower(),))
        return f"✅ Deleted {mvp_name} from database"

    def get_mvp_list(self) -> str:
        mvps = self._execute_db("SELECT name, downtime, spawn_range FROM mvp_database ORDER BY name", fetch=True)
        if not mvps:
            return "No MVPs in database"
        table = PrettyTable(["MVP Name", "Downtime", "Spawn Range"])
        for name, downtime, spawn_range in mvps:
            table.add_row([name, f"{downtime} min", f"{spawn_range} min"])
        return f"""```
MVP Database:
{table}
```"""

    def get_current_time(self) -> str:
        now = datetime.now(self.pst_tz)
        return f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} PST"

    def generate_tracker_table(self) -> str:
        tracked = self._execute_db(
            '''SELECT m.name, t.last_death, t.next_spawn_start, t.next_spawn_end 
               FROM tracked_mvps t JOIN mvp_database m ON t.mvp_name = m.name''',
            fetch=True
        )
        if not tracked:
            return "No MVPs currently being tracked"
        now = datetime.now(self.pst_tz)
        gmt8 = pytz.timezone('Asia/Singapore')
        lines = [
            f"Time: {now.strftime('%Y-%m-%d %I:%M:%S %p')} PST",
            f"      {now.astimezone(gmt8).strftime('%Y-%m-%d %I:%M:%S %p')} GMT+8"
        ]
        for name, last_death, start, end in tracked:
            ld = datetime.fromisoformat(last_death).astimezone(self.pst_tz).strftime('%I:%M %p')
            ns = datetime.fromisoformat(start).astimezone(self.pst_tz)
            ne = datetime.fromisoformat(end).astimezone(self.pst_tz)
            status = ""
            if now < ns:
                status = f"⏳ {(ns - now).seconds // 60}m"
            elif ns <= now <= ne:
                status = f"🔔 {(ne - now).seconds // 60}m"
            else:
                status = "⚠️ Overdue"
            lines.append(f"{name.title():<15} | Last: {ld} | Next: {ns.strftime('%I:%M %p')} PST / {ns.astimezone(gmt8).strftime('%I:%M %p')} GMT+8 | {status}")
            return "```\n" + "\n".join(lines) + "\n```"

    @tasks.loop(seconds=30)
    async def update_tracker(self):
        if self.tracker_channel:
            content = self.generate_tracker_table()
            if self.tracker_message:
                try:
                    await self.tracker_message.edit(content=content)
                except discord.NotFound:
                    self.tracker_message = await self.tracker_channel.send(content)
            if self.time_message:
                try:
                    await self.time_message.edit(content=self.get_current_time())
                except discord.NotFound:
                    self.time_message = await self.tracker_channel.send(self.get_current_time())

    @app_commands.command(name="mvp_add", description="Add or update an MVP in the database")
    async def mvp_add(self, interaction: discord.Interaction, mvp_name: str, downtime: int, spawn_range: int):
        result = self.add_mvp(mvp_name, downtime, spawn_range)
        await interaction.response.send_message(result)

    @app_commands.command(name="mvp_track", description="Start tracking an MVP")
    async def mvp_track(self, interaction: discord.Interaction, mvp_name: str, time_of_death: str = None):
        result = self.track(mvp_name, time_of_death)
        await interaction.response.send_message(result)

    @app_commands.command(name="mvp_remove", description="Stop tracking an MVP")
    async def mvp_remove(self, interaction: discord.Interaction, mvp_name: str):
        result = self.remove(mvp_name)
        await interaction.response.send_message(result)

    @app_commands.command(name="mvp_delete", description="Delete an MVP from database")
    async def mvp_delete(self, interaction: discord.Interaction, mvp_name: str):
        result = self.delete_mvp(mvp_name)
        await interaction.response.send_message(result)

    @app_commands.command(name="mvp_list", description="List all MVPs")
    async def mvp_list(self, interaction: discord.Interaction):
        result = self.get_mvp_list()
        await interaction.response.send_message(result[:1900] if len(result) > 1900 else result)

    @app_commands.command(name="mvp_time", description="Show current time")
    async def mvp_time(self, interaction: discord.Interaction):
        await interaction.response.send_message(self.get_current_time())

    @app_commands.command(name="mvp_refresh", description="Refresh tracker messages")
    async def mvp_refresh(self, interaction: discord.Interaction):
        await self.update_tracker()
        await interaction.response.send_message("✅ Tracker refreshed.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(MVPTrackerCog(bot))
