from twitchio.ext import commands
from models import SessionLocal, Giveaway, User, Item
import random
import asyncio
import sys
import os
import threading
import time

# Use environment variables with fallbacks
BOT_NICK = os.getenv("BOT_NICK", "rafflebot_giveaways")
BOT_TOKEN = os.getenv("BOT_TOKEN", "4gpbhy6ub5fbrn69jsujrma5nkuhqw")  # Should be in environment variables
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
CHANNEL = os.getenv("CHANNEL", "rafflebot_giveaways")

# Dict to track active chatbots by giveaway_id
active_chatbots = {}

# Active giveaway state (can be accessed from different routes/threads)
active_giveaway = None
entries = []
giveaway_task = None
lock = threading.Lock()

# Track bot instances
bot_instances = {}

def is_giveaway_owner(ctx, giveaway):
    db_session = SessionLocal()
    user = db_session.query(User).filter_by(username=ctx.author.name).first()
    db_session.close()
    return user and user.id == giveaway.creator_id

class Bot(commands.Bot):

    def __init__(self, giveaway_id=None):
        super().__init__(token=BOT_TOKEN, prefix=BOT_PREFIX, initial_channels=[CHANNEL])
        self.giveaway_id = giveaway_id
        self._connected_channels = []
        self._nick = BOT_NICK
        self.stopping = False 

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

    async def event_ready(self):
        global active_giveaway, entries

        print(f"Bot is online as {self.nick}!")

        self.connected_channels = list(self.connected_channels) or [CHANNEL] 

        print(f"Connected channels: {self.connected_channels}")

        if not self.connected_channels:
            print("Warning: Bot is not connected to any channels.")
        
        if self.giveaway_id:
            print(f"Auto-starting giveaway ID: {self.giveaway_id}")
            db_session = SessionLocal()
            giveaway = db_session.query(Giveaway).filter_by(id=self.giveaway_id).first()
            
            # Update giveaway status in database
            if giveaway:
                giveaway.active = True
                db_session.commit()
                
            db_session.close()

            if giveaway:
                active_giveaway = giveaway
                entries = []
                print(f"Giveaway '{giveaway.title}' is now active!")
                asyncio.create_task(self.manage_giveaways(None, giveaway))
            else:
                print(f"No giveaway found with ID {self.giveaway_id}")

    async def event_message(self, message):
        if message.author is None:
            return

        print(f"{message.author.name}: {message.content}")

        if message.author.name.lower() == self.nick.lower():
            return

        await self.handle_commands(message)

    @commands.command(name="startgiveaway")
    async def start_giveaway(self, ctx, identifier: str = None):
        global active_giveaway, entries, giveaway_task

        if active_giveaway:
            await ctx.send("A giveaway is already active!")
            return

        if not identifier:
            await ctx.send("Please provide a giveaway ID or title. Use !listgiveaways to see your options.")
            return

        db_session = SessionLocal()
        giveaway = db_session.query(Giveaway).filter_by(id=int(identifier)).first()
        
        # Update giveaway status in database
        if giveaway:
            giveaway.active = True
            db_session.commit()
            
        db_session.close()

        if not giveaway:
            await ctx.send("Invalid giveaway ID provided.")
            return

        active_giveaway = giveaway
        entries = []
        print(f"Starting giveaway: {giveaway.title}")
        await ctx.send(f"A giveaway has started: {giveaway.title}! Type !enter to participate.")
        giveaway_task = asyncio.create_task(self.manage_giveaways(ctx, giveaway))

    @commands.command(name="enter")
    async def enter_giveaway(self, ctx):
        global entries

        if not active_giveaway:
            print("No active giveaway found when entering.")
            await ctx.send("There is no active giveaway to join.")
            return

        with lock:
            if ctx.author.name not in entries:
                entries.append(ctx.author.name)
                print(f"{ctx.author.name} entered the giveaway. Current entries: {entries}")
                await ctx.send(f"{ctx.author.name}, you have been entered into the giveaway!")
            else:
                print(f"{ctx.author.name} is already in the giveaway. Current entries: {entries}")
                await ctx.send(f"{ctx.author.name}, you are already entered!")

    @commands.command(name="endgiveaway")
    async def end_giveaway(self, ctx):
        global active_giveaway, entries, giveaway_task

        if not active_giveaway:
            await ctx.send("There is no active giveaway to end.")
            return

        if giveaway_task:
            giveaway_task.cancel()
            print("Giveaway task canceled.")
            try:
                await giveaway_task
            except asyncio.CancelledError:
                print("Giveaway task cleanup completed.")

        # Update giveaway status in database
        db_session = SessionLocal()
        giveaway = db_session.query(Giveaway).filter_by(id=active_giveaway.id).first()
        if giveaway:
            giveaway.active = False
            db_session.commit()
        db_session.close()

        with lock:
            if entries:
                winner = random.choice(entries)
                await ctx.send(f"The giveaway '{active_giveaway.title}' has ended! Congratulations to {winner}!")
            else:
                await ctx.send(f"The giveaway '{active_giveaway.title}' has ended with no participants.")

        active_giveaway = None
        entries = []

        await ctx.send("Shutting down the giveaway bot. Thank you for participating!")
        print("Initiating bot shutdown...")
        self.stopping = True
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
        global active_giveaway, entries

        try:
            print(f"Managing giveaway: {giveaway.title}")
            db_session = SessionLocal()
            items = db_session.query(Item).filter_by(giveaway_id=giveaway.id, is_won=False).all()
            print(f"Fetched items: {items}")

            if not items:
                print(f"No items found for giveaway '{giveaway.title}'. Ending giveaway.")
                if self.connected_channels:
                    try:
                        channel = self.get_channel(self.connected_channels[0])
                        if channel:
                            await channel.send(
                                f"No items are available for giveaway '{giveaway.title}'. The giveaway cannot proceed."
                            )
                        else:
                            print(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                    except Exception as e:
                        print(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                        
                # Update giveaway status in database
                giveaway = db_session.query(Giveaway).filter_by(id=giveaway.id).first()
                if giveaway:
                    giveaway.active = False
                    db_session.commit()
                
                db_session.close()
                return

            for item in items:
                # Check if we should stop during item processing
                if self.stopping:
                    break
                    
                # Check if giveaway is still active in database
                giveaway_status = db_session.query(Giveaway).filter_by(id=giveaway.id).first()
                
                if not giveaway_status or not giveaway_status.active:
                    print(f"Giveaway {giveaway.id} is no longer active. Stopping processing.")
                    db_session.close()
                    return
                
                print(f"Processing item: {item.name} (ID: {item.id})")

                try:
                    message = f"Giving away: {item.name}!"
                    if self.connected_channels:
                        try:
                            channel = self.get_channel(self.connected_channels[0])
                            if channel:
                                await channel.send(message)
                            else:
                                print(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                        except Exception as e:
                            print(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                    else:
                        print(f"Connected channels not found. Skipping message: {message}")

                    await asyncio.sleep(giveaway.frequency)

                    with lock:
                        if entries:
                            winner_name = random.choice(entries)
                            print(f"Selected winner: {winner_name}")

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
                                        print(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                                except Exception as e:
                                    print(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                            entries.remove(winner_name)
                        else:
                            print(f"No entries found for item: {item.name}")
                            if self.connected_channels:
                                try:
                                    channel = self.get_channel(self.connected_channels[0])
                                    if channel:
                                        await channel.send(
                                            f"No entries for {item.name}. It will be re-given in the next round."
                                        )
                                    else:
                                        print(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                                except Exception as e:
                                    print(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                except Exception as e:
                    print(f"Error processing item '{item.name}': {e}")

            print(f"Giveaway '{giveaway.title}' concluded.")
            if self.connected_channels:
                try:
                    channel = self.get_channel(self.connected_channels[0])
                    if channel:
                        await channel.send(
                            f"The giveaway '{giveaway.title}' has ended. Thank you for participating!"
                        )
                    else:
                        print(f"Channel object for '{self.connected_channels[0]}' not found. Skipping message.")
                except Exception as e:
                    print(f"Error sending message to channel '{self.connected_channels[0]}': {e}")
                    
            # Update giveaway status in database
            giveaway = db_session.query(Giveaway).filter_by(id=giveaway.id).first()
            if giveaway:
                giveaway.active = False
                db_session.commit()
                
            active_giveaway = None

        except Exception as e:
            print(f"Error in managing giveaway: {e}")
        finally:
            db_session.close()
            await self.shutdown()

    async def shutdown(self):
        """Shutdown the bot gracefully."""
        print("Shutting down chatbot...")
        try:
            await self.close()
            print("Bot connection closed.")
        except asyncio.CancelledError:
            print("Suppressed asyncio.CancelledError during shutdown.")
        except Exception as e:
            print(f"Error during bot shutdown: {e}")
        finally:
            # Remove this bot instance from tracking
            if self.giveaway_id and self.giveaway_id in bot_instances:
                del bot_instances[self.giveaway_id]
            
            if hasattr(self, 'giveaway_id') and self.giveaway_id in active_chatbots:
                active_chatbots[self.giveaway_id]['active'] = False


# Function to run the bot in a thread
async def run_bot(giveaway_id):
    bot = Bot(giveaway_id=giveaway_id)
    bot_instances[giveaway_id] = bot
    await bot.start()

# Function to start a giveaway (called from app.py)
def start_chatbot(giveaway_id):
    if giveaway_id in active_chatbots and active_chatbots[giveaway_id]['active']:
        print(f"Chatbot for giveaway {giveaway_id} is already running")
        return False
    
    # Mark this giveaway as having an active chatbot
    active_chatbots[giveaway_id] = {'active': True, 'start_time': time.time()}
    
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Run the bot in this event loop
    loop.run_until_complete(run_bot(giveaway_id))
    return True

# Function to stop a giveaway (called from app.py)
def stop_chatbot(giveaway_id):
    if giveaway_id in bot_instances:
        bot = bot_instances[giveaway_id]
        bot.stopping = True
        return True
    return False

# For standalone testing
if __name__ == "__main__":
    giveaway_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if giveaway_id:
        # This runs when executed directly as a script
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_bot(giveaway_id))
    else:
        print("No giveaway ID provided")
