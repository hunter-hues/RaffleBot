from twitchio.ext import commands
from models import SessionLocal, Giveaway, User, Item, ActiveGiveaway, ProcessTracker
import random
import asyncio
import sys
import os
import threading
import logging
from dotenv import load_dotenv
import psutil
from datetime import datetime
from flask import Flask
from waitress import serve

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)

@app.route('/wake')
def wake():
    return "I'm awake!", 200

# Environment variables
BOT_NICK = os.getenv("BOT_NICK", "rafflebot_giveaways")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
CHANNEL = os.getenv("DEFAULT_CHANNEL", "rafflebot_giveaways")

if not BOT_TOKEN:
    logger.error("Missing required environment variable: BOT_TOKEN")
    raise ValueError("Missing required environment variable: BOT_TOKEN")

active_giveaway = None
entries = []
giveaway_task = None 
lock = threading.Lock()

def is_giveaway_owner(ctx, giveaway):
    db_session = SessionLocal()
    user = db_session.query(User).filter_by(username=ctx.author.name).first()
    db_session.close()
    return user and user.id == giveaway.creator_id

class Bot(commands.Bot):

    def __init__(self, giveaway_id=None, channel_name=None):
        initial_channel = channel_name or CHANNEL
        super().__init__(token=BOT_TOKEN, prefix=BOT_PREFIX, initial_channels=[initial_channel])
        self.giveaway_id = giveaway_id
        self._connected_channels = []
        self._nick = BOT_NICK 
        self.empty_rounds = 0
        self.channel_name = channel_name
        self.logger = logging.getLogger(f"Bot-{channel_name or 'default'}")
        self.active_giveaway = None
        self.entries = []
        self.giveaway_task = None
        self.heartbeat_task = None
        self.process_id = os.getpid()

    @property
    def connected_channels(self):
        return self._connected_channels

    @connected_channels.setter
    def connected_channels(self, channels):
        self._connected_channels = channels

    @property
    def nick(self):
        return self._nick

    @nick.setter
    def nick(self, value):
        self._nick = value

    async def update_heartbeat(self):
        """Update the process heartbeat in the database."""
        while True:
            try:
                db_session = SessionLocal()
                tracker = db_session.query(ProcessTracker).filter_by(
                    process_id=self.process_id,
                    giveaway_id=self.giveaway_id
                ).first()
                
                if tracker:
                    tracker.last_heartbeat = datetime.utcnow()
                    db_session.commit()
                db_session.close()
            except Exception as e:
                self.logger.error(f"Error updating heartbeat: {e}")
            await asyncio.sleep(30)  # Update every 30 seconds

    async def event_ready(self):
        """Called once when the bot goes online."""
        self.logger.info(f"Bot is ready! Connected to channel: {self.channel_name}")
        if self.giveaway_id:
            self.logger.info(f"Managing giveaway ID: {self.giveaway_id}")

        self.connected_channels = list(self.connected_channels) or [self.channel_name or CHANNEL] 
        self.logger.info(f"Connected channels: {self.connected_channels}")

        if not self.connected_channels:
            self.logger.warning("Bot is not connected to any channels.")
        
        if self.giveaway_id:
            self.logger.info(f"Auto-starting giveaway ID: {self.giveaway_id}")
            db_session = SessionLocal()
            try:
                # Clean up any stale process trackers
                stale_trackers = db_session.query(ProcessTracker).filter_by(
                    giveaway_id=self.giveaway_id,
                    is_active=True
                ).all()
                
                for tracker in stale_trackers:
                    try:
                        process = psutil.Process(tracker.process_id)
                        if not process.is_running():
                            tracker.is_active = False
                            self.logger.info(f"Marked stale tracker {tracker.process_id} as inactive")
                    except psutil.NoSuchProcess:
                        tracker.is_active = False
                        self.logger.info(f"Marked non-existent tracker {tracker.process_id} as inactive")
                
                db_session.commit()

                # Check if there's already an active giveaway with a running process
                active = db_session.query(ActiveGiveaway).filter_by(giveaway_id=self.giveaway_id).first()
                if active:
                    try:
                        # Check if the process is still running
                        process = psutil.Process(active.process_id)
                        if process.is_running():
                            # Check if the process is actually our bot
                            if active.process_id != self.process_id:
                                # Another process is already running this giveaway
                                self.logger.warning(f"Another process (PID: {active.process_id}) is already running this giveaway. Exiting gracefully.")
                                # Instead of calling shutdown(), just close the connection
                                await self.close()
                                return
                            else:
                                # This is our own process, we can continue
                                self.logger.info(f"This is our own process (PID: {active.process_id}), continuing with giveaway")
                        else:
                            # Process is not running, clean up the stale entry
                            self.logger.info(f"Cleaning up stale active giveaway entry for process {active.process_id}")
                            db_session.delete(active)
                            db_session.commit()
                    except psutil.NoSuchProcess:
                        # Process doesn't exist, clean up the stale entry
                        self.logger.info(f"Cleaning up stale active giveaway entry for non-existent process {active.process_id}")
                        db_session.delete(active)
                        db_session.commit()

                giveaway = db_session.query(Giveaway).filter_by(id=self.giveaway_id).first()
                if giveaway:
                    # Create new process tracker
                    tracker = ProcessTracker(
                        process_id=self.process_id,
                        giveaway_id=self.giveaway_id
                    )
                    db_session.add(tracker)
                    
                    # Create or update active giveaway entry
                    active = db_session.query(ActiveGiveaway).filter_by(giveaway_id=self.giveaway_id).first()
                    if active:
                        active.process_id = self.process_id
                    else:
                        active = ActiveGiveaway(
                            giveaway_id=self.giveaway_id,
                            process_id=self.process_id
                        )
                        db_session.add(active)
                    
                    db_session.commit()

                    self.active_giveaway = giveaway
                    self.entries = []
                    self.empty_rounds = 0
                    self.logger.info(f"Giveaway '{giveaway.title}' is now active with threshold: {giveaway.threshold}")
                    self.giveaway_task = asyncio.create_task(self.manage_giveaways(None, giveaway))
                    self.heartbeat_task = asyncio.create_task(self.update_heartbeat())
                else:
                    self.logger.error(f"No giveaway found with ID {self.giveaway_id}")
                    await self.close()
            except Exception as e:
                self.logger.error(f"Error starting giveaway: {str(e)}")
                await self.close()
            finally:
                db_session.close()

    async def event_message(self, message):
        if message.author is None:
            return

        self.logger.debug(f"{message.author.name}: {message.content}")

        if message.author.name.lower() == self.nick.lower():
            return

        await self.handle_commands(message)

    @commands.command(name="startgiveaway")
    async def start_giveaway(self, ctx, identifier: str = None):
        if self.active_giveaway:
            await ctx.send("A giveaway is already active!")
            return

        if not identifier:
            await ctx.send("Please provide a giveaway ID or title. Use !listgiveaways to see your options.")
            return

        db_session = SessionLocal()
        giveaway = db_session.query(Giveaway).filter_by(id=int(identifier)).first()
        db_session.close()

        if not giveaway:
            await ctx.send("Invalid giveaway ID provided.")
            return

        self.active_giveaway = giveaway
        self.entries = []
        self.empty_rounds = 0  # Reset empty rounds counter when starting a new giveaway
        print(f"Starting giveaway: {giveaway.title} with threshold: {giveaway.threshold}")
        await ctx.send(f"A giveaway has started: {giveaway.title}! Type !enter to participate.")
        self.giveaway_task = asyncio.create_task(self.manage_giveaways(ctx, giveaway))

    @commands.command(name="enter")
    async def enter_giveaway(self, ctx):
        if not self.giveaway_id:
            await ctx.send("There is no active giveaway to join.")
            return

        db_session = SessionLocal()
        try:
            # Check if this is the active process for this giveaway
            active = db_session.query(ActiveGiveaway).filter_by(giveaway_id=self.giveaway_id).first()
            if not active:
                await ctx.send("There is no active giveaway to join.")
                return
            
            # Only the process with the matching process_id should respond to entries
            if active.process_id != self.process_id:
                self.logger.info(f"Ignoring entry from {ctx.author.name} as this is not the active process for giveaway {self.giveaway_id}")
                return

            with lock:
                if ctx.author.name not in self.entries:
                    self.entries.append(ctx.author.name)
                    self.logger.info(f"{ctx.author.name} entered the giveaway. Current entries: {self.entries}")
                    await ctx.send(f"{ctx.author.name}, you have been entered into the giveaway!")
                else:
                    self.logger.info(f"{ctx.author.name} is already in the giveaway. Current entries: {self.entries}")
                    await ctx.send(f"{ctx.author.name}, you are already entered!")
        finally:
            db_session.close()

    @commands.command(name="endgiveaway")
    async def end_giveaway(self, ctx):
        if not self.active_giveaway:
            await ctx.send("There is no active giveaway to end.")
            return

        if self.giveaway_task:
            self.giveaway_task.cancel()
            print("Giveaway task canceled.")
            try:
                await self.giveaway_task
            except asyncio.CancelledError:
                print("Giveaway task cleanup completed.")

        with lock:
            if self.entries:
                winner = random.choice(self.entries)
                await ctx.send(f"The giveaway '{self.active_giveaway.title}' has ended! Congratulations to {winner}!")
            else:
                await ctx.send(f"The giveaway '{self.active_giveaway.title}' has ended with no participants.")

        self.active_giveaway = None
        self.entries = []
        self.empty_rounds = 0  # Reset empty rounds counter when ending a giveaway manually

        await ctx.send("Shutting down the giveaway bot. Thank you for participating!")
        print("Initiating bot shutdown...")
        await self.shutdown()

    @commands.command(name="listgiveaways")
    async def list_giveaways(self, ctx):
        db_session = SessionLocal()
        user = db_session.query(User).filter_by(username=ctx.author.name).first()

        if not user:
            await ctx.send("You are not authorized to list giveaways.")
            db_session.close()
            return

        giveaways = db_session.query(Giveaway).filter_by(creator_id=user.id).all()
        db_session.close()

        if not giveaways:
            await ctx.send("You have no giveaways available.")
            return

        giveaway_list = ", ".join([f"ID #{g.id}: {g.title}" for g in giveaways])
        await ctx.send(f"Your giveaways: {giveaway_list}")

    async def manage_giveaways(self, ctx, giveaway):
        try:
            self.logger.info(f"Managing giveaway: {giveaway.title}")
            self.logger.info(f"Giveaway details - ID: {giveaway.id}, Frequency: {giveaway.frequency}, Threshold: {giveaway.threshold}")
            
            db_session = SessionLocal()
            items = db_session.query(Item).filter_by(giveaway_id=giveaway.id, is_won=False).all()
            self.logger.info(f"Fetched items: {items}")

            if not items:
                self.logger.info(f"No items found for giveaway '{giveaway.title}'. Ending giveaway.")
                if self.connected_channels:
                    try:
                        channel = self.get_channel(self.connected_channels[0])
                        if channel:
                            await channel.send(
                                f"No items are available for giveaway '{giveaway.title}'. The giveaway cannot proceed."
                            )
                        else:
                            self.logger.warning(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                    except Exception as e:
                        self.logger.error(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                return

            for item in items:
                self.logger.debug(f"Processing item: {item.name} (ID: {item.id})")

                try:
                    message = f"Giving away: {item.name}!"
                    if self.connected_channels:
                        try:
                            channel = self.get_channel(self.connected_channels[0])
                            if channel:
                                await channel.send(message)
                            else:
                                self.logger.warning(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                        except Exception as e:
                            self.logger.error(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                    else:
                        self.logger.warning(f"Connected channels not found. Skipping message: {message}")

                    await asyncio.sleep(giveaway.frequency)

                    with lock:
                        if self.entries:
                            winner_name = random.choice(self.entries)
                            self.logger.info(f"Selected winner: {winner_name}")

                            winner = db_session.query(User).filter_by(username=winner_name).first()

                            item.is_won = True
                            if winner:
                                item.winner_id = winner.id  
                            item.winner_username = winner_name 
                            db_session.commit()

                            if self.connected_channels:
                                try:
                                    channel = self.get_channel(self.connected_channels[0])
                                    if channel:
                                        await channel.send(
                                            f"Congratulations {winner_name}! You've won {item.name}!"
                                        )
                                    else:
                                        self.logger.warning(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                                except Exception as e:
                                    self.logger.error(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                            self.entries.remove(winner_name)
                        else:
                            self.logger.info(f"No entries found for item: {item.name}")
                            self.empty_rounds += 1  # Increment empty rounds counter
                            self.logger.info(f"Empty rounds: {self.empty_rounds}/{giveaway.threshold}")
                            
                            if self.empty_rounds >= giveaway.threshold:
                                self.logger.info(f"THRESHOLD REACHED: {self.empty_rounds} empty rounds >= threshold of {giveaway.threshold}")
                                if self.connected_channels:
                                    try:
                                        channel = self.get_channel(self.connected_channels[0])
                                        if channel:
                                            await channel.send(
                                                f"No entries for {self.empty_rounds} consecutive rounds. Giveaway '{giveaway.title}' has been automatically ended."
                                            )
                                        else:
                                            self.logger.warning(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                                    except Exception as e:
                                        self.logger.error(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                                self.logger.info(f"Ending giveaway '{giveaway.title}' due to {self.empty_rounds} empty rounds")
                                break  # Exit the loop to end the giveaway
                            else:
                                self.logger.info(f"Threshold not yet reached: {self.empty_rounds} empty rounds < threshold of {giveaway.threshold}")
                            
                            if self.connected_channels:
                                try:
                                    channel = self.get_channel(self.connected_channels[0])
                                    if channel:
                                        await channel.send(
                                            f"No entries for {item.name}. It will be re-given in the next round. ({self.empty_rounds}/{giveaway.threshold} empty rounds)"
                                        )
                                    else:
                                        self.logger.warning(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                                except Exception as e:
                                    self.logger.error(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                except Exception as e:
                    self.logger.error(f"Error processing item '{item.name}': {e}")

            self.logger.info(f"Giveaway '{giveaway.title}' concluded.")
            if self.connected_channels:
                try:
                    channel = self.get_channel(self.connected_channels[0])
                    if channel:
                        await channel.send(
                            f"The giveaway '{giveaway.title}' has ended. Thank you for participating!"
                        )
                    else:
                        self.logger.warning(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                except Exception as e:
                    self.logger.error(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
            self.active_giveaway = None

        except Exception as e:
            self.logger.error(f"Error in managing giveaway: {e}")
        finally:
            db_session.close()
            await self.shutdown()

    async def shutdown(self):
        """Clean up when the bot shuts down."""
        self.logger.info("Initiating bot shutdown...")
        try:
            db_session = SessionLocal()
            # Mark process tracker as inactive
            tracker = db_session.query(ProcessTracker).filter_by(
                process_id=self.process_id,
                giveaway_id=self.giveaway_id
            ).first()
            if tracker:
                tracker.is_active = False
                db_session.commit()
                self.logger.info(f"Marked process tracker {self.process_id} as inactive")

            # Remove active giveaway record
            active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=self.giveaway_id).first()
            if active_giveaway:
                db_session.delete(active_giveaway)
                db_session.commit()
                self.logger.info(f"Removed active giveaway record for giveaway {self.giveaway_id}")
            db_session.close()
        except Exception as e:
            self.logger.error(f"Error during shutdown cleanup: {e}")
        finally:
            # Cancel any running tasks
            if self.heartbeat_task and not self.heartbeat_task.done():
                self.heartbeat_task.cancel()
                try:
                    await self.heartbeat_task
                except asyncio.CancelledError:
                    pass
            
            if self.giveaway_task and not self.giveaway_task.done():
                self.giveaway_task.cancel()
                try:
                    await self.giveaway_task
                except asyncio.CancelledError:
                    pass
            
            # Close the bot connection instead of calling super().shutdown()
            self.logger.info("Closing bot connection...")
            await self.close()
            self.logger.info("Bot shutdown complete")


if __name__ == "__main__":
    giveaway_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    channel_name = sys.argv[2] if len(sys.argv) > 2 else None
    
    if channel_name:
        print(f"Starting bot in channel: {channel_name}")
    else:
        print(f"No channel specified, using default channel: {CHANNEL}")
    
    bot = Bot(giveaway_id=giveaway_id, channel_name=channel_name)
    
    # Set up signal handlers for graceful shutdown
    import signal
    
    def signal_handler(sig, frame):
        print(f"Received signal {sig}, shutting down...")
        asyncio.create_task(bot.shutdown())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start Flask server in a separate thread using waitress
    threading.Thread(target=serve, args=(app,), kwargs={"host": "0.0.0.0", "port": 5001}, daemon=True).start()
    
    # Run the bot
    bot.run()
