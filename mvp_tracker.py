import json

import discord
from discord.ext import commands, tasks
import logging
from dotenv import load_dotenv
import os
import datetime
import pytz
from typing import Dict
from prettytable import PrettyTable

# Bot setup
load_dotenv()
token = os.getenv('DISCORD_TOKEN')

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

class MVPTracker:
    def __init__(self):
        self.mvp_database: Dict[str, dict] = {}
        self.tracked_mvps: Dict[str, dict] = {}
        self.pst_tz = pytz.timezone('America/Los_Angeles')
        self.tracker_message = None
        self.tracker_channel = None
        self.permanent_channel_id = 1374762488234639401
        self.update_loop = None
        self.time_loop = None

        # Load both databases on initialization
        self._load_mvp_database()
        self._load_tracked_mvps()

    class MVPBot(commands.Bot):
        def __init__(self):
            intents = discord.Intents.default()
            intents.message_content = True
            intents.members = True

            super().__init__(command_prefix='!', intents=intents)
            self.mvp_tracker = MVPTracker()

        async def setup_hook(self):
            # Start background tasks
            self.update_permatracker.start()

        @tasks.loop(seconds=30)
        async def update_permatracker(self):
            """Update permatracker every 30 seconds"""
            if self.mvp_tracker.tracker_message and self.mvp_tracker.time_message:
                try:
                    await self.mvp_tracker.time_message.edit(content=self.mvp_tracker.generate_time_message())
                    await self.mvp_tracker.tracker_message.edit(content=self.mvp_tracker.generate_tracker_table())
                except discord.NotFound:
                    # Messages were deleted, recreate them
                    await self.mvp_tracker.initialize_permatracker(self)
                except Exception as e:
                    print(f"Error updating permatracker: {e}")

        @update_permatracker.before_loop
        async def before_update(self):
            await self.wait_until_ready()

        async def on_ready(self):
            print(f'Logged in as {self.user}')
            # Initialize permatracker
            await self.mvp_tracker.initialize_permatracker(self)
            print("Bot is ready!")

    def _load_mvp_database(self):
        """Load MVP database from mvp.json"""
        try:
            with open('mvp.json', 'r') as f:
                self.mvp_database = json.load(f)
        except FileNotFoundError:
            self.mvp_database = {}
            self._save_mvp_database()
        except json.JSONDecodeError:
            print("Error decoding mvp.json, creating new database")
            self.mvp_database = {}
            self._save_mvp_database()

    def _save_mvp_database(self):
        """Save MVP database to mvp.json"""
        try:
            with open('mvp.json', 'w') as f:
                json.dump(self.mvp_database, f, indent=4)
        except Exception as e:
            print(f"Error saving MVP database: {e}")

    def _load_tracked_mvps(self):
        """Load tracked MVPs from tracked_mvps.json"""
        try:
            with open('tracked_mvps.json', 'r') as f:
                tracked_data = json.load(f)
                self.tracked_mvps = {}
                for mvp_name, track_info in tracked_data.items():
                    self.tracked_mvps[mvp_name] = {
                        "tracking_since": datetime.datetime.fromisoformat(track_info["tracking_since"]).astimezone(self.pst_tz),
                        "last_death": datetime.datetime.fromisoformat(track_info["last_death"]).astimezone(self.pst_tz) if track_info["last_death"] else None,
                        "next_spawn_start": datetime.datetime.fromisoformat(track_info["next_spawn_start"]).astimezone(self.pst_tz) if track_info["next_spawn_start"] else None,
                        "next_spawn_end": datetime.datetime.fromisoformat(track_info["next_spawn_end"]).astimezone(self.pst_tz) if track_info["next_spawn_end"] else None
                    }
        except FileNotFoundError:
            self.tracked_mvps = {}
        except json.JSONDecodeError:
            print("Error decoding tracked_mvps.json, creating new tracking data")
            self.tracked_mvps = {}

    def _save_tracked_mvps(self):
        """Save tracked MVPs to tracked_mvps.json"""
        tracked_data = {}
        for mvp_name, track_info in self.tracked_mvps.items():
            tracked_data[mvp_name] = {
                "tracking_since": track_info["tracking_since"].isoformat(),
                "last_death": track_info["last_death"].isoformat() if track_info["last_death"] else None,
                "next_spawn_start": track_info["next_spawn_start"].isoformat() if track_info["next_spawn_start"] else None,
                "next_spawn_end": track_info["next_spawn_end"].isoformat() if track_info["next_spawn_end"] else None
            }
        try:
            with open('tracked_mvps.json', 'w') as f:
                json.dump(tracked_data, f, indent=4)
        except Exception as e:
            print(f"Error saving tracked MVPs: {e}")

    def add_mvp(self, mvp_name: str, downtime: int, spawn_range: int) -> str:
        """Add or update an MVP in the database"""
        mvp_name = mvp_name.lower()
        self.mvp_database[mvp_name] = {
            "name": mvp_name,
            "downtime": downtime,
            "spawn_range": spawn_range
        }
        self._save_mvp_database()
        return f"✅ Added/Updated {mvp_name} (DT: {downtime}m, SR: {spawn_range}m)"

    def track(self, mvp_name: str, time_of_death: str = None) -> str:
        """Track an MVP"""
        mvp_name = mvp_name.lower()
        if mvp_name not in self.mvp_database:
            return f"❌ {mvp_name} not found in database"

        now = datetime.datetime.now(self.pst_tz)

        if time_of_death:
            try:
                hours, minutes = map(int, time_of_death.split(':'))
                death_time = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)

                if death_time > now:
                    death_time -= datetime.timedelta(days=1)

                mvp_info = self.mvp_database[mvp_name]
                next_spawn_start = death_time + datetime.timedelta(minutes=mvp_info['downtime'])
                next_spawn_end = next_spawn_start + datetime.timedelta(minutes=mvp_info['spawn_range'])
            except ValueError:
                return "❌ Invalid time format. Use HH:MM (24-hour format)"
        else:
            death_time = now
            mvp_info = self.mvp_database[mvp_name]
            next_spawn_start = death_time + datetime.timedelta(minutes=mvp_info['downtime'])
            next_spawn_end = next_spawn_start + datetime.timedelta(minutes=mvp_info['spawn_range'])

        self.tracked_mvps[mvp_name] = {
            "tracking_since": now,
            "last_death": death_time,
            "next_spawn_start": next_spawn_start,
            "next_spawn_end": next_spawn_end
        }

        # Save tracking data
        self._save_tracked_mvps()

        return f"✅ Now tracking {mvp_info['name']}"

    def remove(self, mvp_name: str) -> str:
        """Remove an MVP from tracking"""
        mvp_name = mvp_name.lower()
        if mvp_name in self.tracked_mvps:
            del self.tracked_mvps[mvp_name]
            self._save_tracked_mvps()
            return f"✅ Removed {self.mvp_database[mvp_name]['name']} from tracker"
        return f"❌ {mvp_name} is not being tracked"

    def delete_mvp(self, mvp_name: str) -> str:
        """Delete an MVP from the database"""
        mvp_name = mvp_name.lower()
        if mvp_name in self.mvp_database:
            name = self.mvp_database[mvp_name]['name']
            del self.mvp_database[mvp_name]
            if mvp_name in self.tracked_mvps:
                del self.tracked_mvps[mvp_name]
                self._save_tracked_mvps()
            self._save_mvp_database()
            return f"✅ Deleted {name} from database"
        return f"❌ {mvp_name} not found in database"

    def get_mvp_list(self) -> str:
        print(f"Current MVP database size: {len(self.mvp_database)}")  # Debug line
        if not self.mvp_database:
            return "No MVPs in database"

        table = PrettyTable()
        table.field_names = ["MVP Name", "Downtime", "Spawn Range"]
        table.align = "l"

        for mvp_data in self.mvp_database.values():
            table.add_row([
                mvp_data['name'],
                f"{mvp_data['downtime']} min",
                f"{mvp_data['spawn_range']} min"
            ])

        return f"```\nMVP Database:\n{table}\n```"

    def get_current_time(self) -> str:
        now = datetime.datetime.now(self.pst_tz)
        return f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} PST"

    def generate_tracker_table(self) -> str:
        if not self.tracked_mvps:
            return "No MVPs currently being tracked"

        now = datetime.datetime.now(self.pst_tz)
        gmt8_tz = pytz.timezone('Asia/Singapore')  # Singapore uses GMT+8
        current_time_pst = now.strftime('%Y-%m-%d %I:%M:%S %p PST')
        current_time_gmt8 = now.astimezone(gmt8_tz).strftime('%Y-%m-%d %I:%M:%S %p GMT+8')

        # Calculate maximum widths for each column
        max_name_len = max([len(self.mvp_database[mvp]['name']) for mvp in self.tracked_mvps] + [4])

        # Modified template to accommodate AM/PM format
        template = "{:<{name_width}} │ {:<11} │ {:<14} │ {:<11} │ {:<14}"
        divider = "─" * max_name_len + "─┬─" + "─" * 11 + "─┬─" + "─" * 14 + "─┬─" + "─" * 11 + "─┬─" + "─" * 14

        # Build table
        lines = []
        lines.append(f"Time: {current_time_pst}")
        lines.append(f"      {current_time_gmt8}")
        lines.append(divider)
        lines.append(template.format("MVP", "Last", "Next (PST)", "GMT+8", "Status", name_width=max_name_len))
        lines.append(divider)

        for mvp_name, track_data in self.tracked_mvps.items():
            mvp_info = self.mvp_database[mvp_name]
            name = mvp_info['name']

            last_death = track_data['last_death']
            next_spawn_start = track_data['next_spawn_start']
            next_spawn_end = track_data['next_spawn_end']

            if last_death is None:
                status = "❓ No record"
                last_death_str = "---"
                next_spawn_str = "---"
                next_spawn_gmt8_str = "---"
            else:
                last_death_str = last_death.strftime('%I:%M %p')
                next_spawn_str = next_spawn_start.strftime('%I:%M %p')

                # Convert to GMT+8
                next_spawn_gmt8 = next_spawn_start.astimezone(gmt8_tz)
                next_spawn_gmt8_str = next_spawn_gmt8.strftime('%I:%M %p')

                if now < next_spawn_start:
                    time_remaining = next_spawn_start - now
                    minutes_remaining = int(time_remaining.total_seconds() / 60)
                    status = f"⏳ {minutes_remaining}m"
                elif next_spawn_start <= now <= next_spawn_end:
                    time_remaining = next_spawn_end - now
                    minutes_remaining = int(time_remaining.total_seconds() / 60)
                    status = f"🔔 {minutes_remaining}m"
                else:
                    status = "⚠️ Overdue"

            lines.append(template.format(
                name,
                last_death_str,
                next_spawn_str,
                next_spawn_gmt8_str,
                status,
                name_width=max_name_len
            ))

        return f"```\n" + "\n".join(lines) + "\n```"

    def _save_tracked_mvps(self):
        """Save tracked MVPs to file"""
        tracked_data = {}
        for mvp_name, track_info in self.tracked_mvps.items():
            tracked_data[mvp_name] = {
                "tracking_since": track_info["tracking_since"].isoformat(),
                "last_death": track_info["last_death"].isoformat() if track_info["last_death"] else None,
                "next_spawn_start": track_info["next_spawn_start"].isoformat() if track_info["next_spawn_start"] else None,
                "next_spawn_end": track_info["next_spawn_end"].isoformat() if track_info["next_spawn_end"] else None
            }
        try:
            with open('tracked_mvps.json', 'w') as f:
                json.dump(tracked_data, f, indent=4)
        except Exception as e:
            print(f"Error saving tracked MVPs: {e}")

    async def initialize_permatracker(self, bot):
        """Initialize the permatracker in the specified channel"""
        try:
            channel = bot.get_channel(1374762488234639401)  # Your permatracker channel ID
            if not channel:
                print("Could not find permatracker channel!")
                return False

            # Create/Update tracker message
            if not self.tracker_message:
                self.tracker_message = await channel.send(self.generate_tracker_table())
            else:
                await self.tracker_message.edit(content=self.generate_tracker_table())

            self.tracker_channel = channel
            self.permanent_channel_id = channel.id

            # Start update loop if it's not running
            if not self.update_loop or self.update_loop.is_running() is False:
                self.update_loop = tasks.loop(seconds=15)(self.update_tracker_message)
                self.update_loop.start()

            return True

        except Exception as e:
            print(f"Error initializing permatracker: {e}")
            return False

    async def update_tracker_message(self):
        """Update the tracker message content"""
        if self.tracker_message and self.tracker_channel:
            try:
                await self.tracker_message.edit(content=self.generate_tracker_table())
                print("Tracker updated successfully")  # Debug message
            except discord.NotFound:
                # If message was deleted, create a new one
                self.tracker_message = await self.tracker_channel.send(self.generate_tracker_table())
                print("Tracker message recreated")  # Debug message
            except Exception as e:
                print(f"Error updating tracker message: {e}")


mvp_tracker = MVPTracker()


@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    # Initialize permatracker
    success = await mvp_tracker.initialize_permatracker(bot)
    if success:
        print("Permatracker initialized and update loop started")
    else:
        print("Failed to initialize permatracker")

    # Specify your permatracker channel ID
    channel_id = 1374762488234639401
    channel = bot.get_channel(channel_id)

    if channel:
        try:
            # Create time message first
            time_message = await channel.send(mvp_tracker.get_current_time())
            mvp_tracker.time_message = time_message

            # Create tracker message with initial MVP data
            tracker_message = await channel.send(mvp_tracker.generate_tracker_table())
            mvp_tracker.tracker_message = tracker_message
            mvp_tracker.tracker_channel = channel
            mvp_tracker.permanent_channel_id = channel_id

            # Start the update loops
            update_tracker.start()
            update_time.start()

            print("Permatracker initialized successfully!")
        except Exception as e:
            print(f"Failed to initialize permatracker: {e}")
    else:
        print("Could not find permatracker channel!")

@tasks.loop(seconds=30)
async def update_tracker():
    if mvp_tracker.tracker_message and mvp_tracker.tracker_channel:
        try:
            await mvp_tracker.tracker_message.edit(content=mvp_tracker.generate_tracker_table())
        except discord.errors.NotFound:
            # Recreate the tracker message if it was deleted
            channel = bot.get_channel(mvp_tracker.permanent_channel_id)
            if channel:
                message = await channel.send(mvp_tracker.generate_tracker_table())
                mvp_tracker.tracker_message = message
                mvp_tracker.tracker_channel = channel

@tasks.loop(seconds=1)
async def update_time():
    if mvp_tracker.time_message:
        try:
            await mvp_tracker.time_message.edit(content=mvp_tracker.get_current_time())
        except discord.errors.NotFound:
            # Recreate the time message if it was deleted
            channel = bot.get_channel(mvp_tracker.permanent_channel_id)
            if channel:
                message = await channel.send(mvp_tracker.get_current_time())
                mvp_tracker.time_message = message

@update_tracker.error
async def tracker_error(error):
    print(f"Error in tracker update task: {error}")

@update_time.error
async def time_error(error):
    print(f"Error in time update task: {error}")

    channel_id = 1374762488234639401  # Your #permatracker channel ID
    channel = bot.get_channel(channel_id)
    if channel:
        # Initialize permatracker on startup
        success = await bot.mvp_tracker.initialize_permatracker(bot)
        if success:
            print("Permatracker initialized successfully")
        else:
            print("Failed to initialize permatracker")

    @bot.command(name='permatracker')
    async def permatracker(ctx):
        """Manual permatracker refresh command"""
        if ctx.channel.id == bot.mvp_tracker.permatracker_channel_id:
            await bot.mvp_tracker.initialize_permatracker(bot)
            await ctx.message.delete()  # Remove the command message
        else:
            await ctx.send("This command can only be used in the permatracker channel!")


    @tasks.loop(seconds=30)
    async def permatracker_update_loop():
        await bot.mvp_tracker.update_permatracker()

    # Error handler for the update loop
    @permatracker_update_loop.error
    async def permatracker_update_error(error):
        print(f"Error in permatracker update loop: {error}")

        # Create time message
        time_message = await channel.send(mvp_tracker.generate_time_message())
        mvp_tracker.time_message = time_message

        # Create tracker message with loaded tracked MVPs
        tracker_message = await channel.send(mvp_tracker.generate_tracker_table())
        mvp_tracker.tracker_message = tracker_message
        mvp_tracker.tracker_channel = channel
        mvp_tracker.permanent_channel_id = channel_id

        # Load MVP data first
        mvp_tracker._load_tracked_mvps()

        # Initialize the tracker messages
        success = await mvp_tracker.initialize_tracker(bot)
        if success:
            print("Permatracker initialized successfully!")
        else:
            print("Failed to initialize permatracker")

        # Start the update loops
        update_tracker.start()
        update_time.start()
async def update_tracker_message(self):
    """Update the tracker message content"""
    if self.tracker_message:
        try:
            await self.tracker_message.edit(content=self.generate_tracker_table())
        except discord.errors.NotFound:
            # If message was deleted, create a new one
            if self.tracker_channel:
                self.tracker_message = await self.tracker_channel.send(self.generate_tracker_table())

async def update_time_message(self):
    """Update the time message content"""
    if self.time_message:
        try:
            await self.time_message.edit(content=self.generate_time_message())
        except discord.errors.NotFound:
            # If message was deleted, create a new one
            if self.tracker_channel:
                self.time_message = await self.tracker_channel.send(self.generate_time_message())

@tasks.loop(seconds=1)
async def update_tracker():
    if mvp_tracker.tracker_message and mvp_tracker.tracker_channel:
        try:
            await mvp_tracker.tracker_message.edit(content=mvp_tracker.generate_tracker_table())
        except discord.errors.NotFound:
            # Recreate the tracker message if it was deleted
            if mvp_tracker.permanent_channel_id:
                channel = bot.get_channel(mvp_tracker.permanent_channel_id)
                if channel:
                    message = await channel.send(mvp_tracker.generate_tracker_table())
                    mvp_tracker.tracker_message = message
                    mvp_tracker.tracker_channel = channel

@tasks.loop(seconds=1)
async def update_time():
    if mvp_tracker.time_message:
        try:
            await mvp_tracker.time_message.edit(content=mvp_tracker.generate_time_message())
        except discord.errors.NotFound:
            # Recreate the time message if it was deleted
            if mvp_tracker.permanent_channel_id:
                channel = bot.get_channel(mvp_tracker.permanent_channel_id)
                if channel:
                    message = await channel.send(mvp_tracker.generate_time_message())
                    mvp_tracker.time_message = message

@tasks.loop(seconds=30)
async def update_tracker():
    await mvp_tracker.update_tracker_message()

@tasks.loop(seconds=1)
async def update_time():
    await mvp_tracker.update_time_message()

@update_tracker.error
async def tracker_error(error):
    print(f"Error in tracker update task: {error}")

@update_time.error
async def time_error(error):
    print(f"Error in time update task: {error}")

@bot.command(name='addmvp')
async def add_mvp(ctx, mvp_name: str, downtime: int, spawn_range: int):
    result = mvp_tracker.add_mvp(mvp_name, downtime, spawn_range)
    await ctx.send(result)

@bot.command(name='track')
async def track(ctx, mvp_name: str, time_of_death: str = None):
    result = mvp_tracker.track(mvp_name, time_of_death)
    await ctx.send(result)


@bot.command(name='permatracker')
async def permatracker(ctx):
    message = await ctx.send(mvp_tracker.generate_tracker_table())
    mvp_tracker.tracker_message = message
    mvp_tracker.tracker_channel = ctx.channel

@bot.command(name='remove')
async def remove(ctx, mvp_name: str):
    result = mvp_tracker.remove(mvp_name)
    await ctx.send(result)

@bot.command(name='deletemvp')
async def delete_mvp(ctx, mvp_name: str):
    result = mvp_tracker.delete_mvp(mvp_name)
    await ctx.send(result)

@bot.command(name='mvplist')
async def mvp_list(ctx):
    try:
        mvp_tracker._load_mvp_database()
        result = mvp_tracker.get_mvp_list()

        # Remove the triple backticks and split the table
        if result.startswith("```\n"):
            result = result[4:]  # Remove leading ```\n
        if result.endswith("\n```"):
            result = result[:-4]  # Remove trailing \n```

        # Split the content into chunks of 1900 characters (leaving room for backticks)
        chunks = [result[i:i + 1900] for i in range(0, len(result), 1900)]

        # Send each chunk as a separate message
        for i, chunk in enumerate(chunks):
            # Add code block formatting to each chunk
            formatted_chunk = f"```\n{chunk}\n```"
            await ctx.send(formatted_chunk)

    except Exception as e:
        error_message = f"Error occurred: {str(e)}"
        print(error_message)
        await ctx.send(error_message)



@bot.command(name='time')
async def current_time(ctx):
    result = mvp_tracker.get_current_time()
    await ctx.send(result)

@bot.command(name='refresh')
async def refresh_command(ctx):
    """Force an immediate permatracker update"""
    if ctx.channel.id == mvp_tracker.permanent_channel_id:
        await mvp_tracker.update_tracker_message()
        await ctx.message.delete()
    else:
        await ctx.send("This command can only be used in the permatracker channel!")

if __name__ == "__main__":
    bot.run(token, log_handler=handler, log_level=logging.DEBUG)