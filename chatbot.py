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
from flask import Flask, session, redirect, request, render_template, jsonify
from waitress import serve
from flask_session import Session
import requests
from functools import wraps
import time
from sqlalchemy import text

# Load environment variables only in development
if os.getenv('FLASK_ENV') != 'production':
    load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create console handler with formatting
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Configure the root logger to show all logs
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(console_handler)

# Create Flask app
app = Flask(__name__)

# Global bot instance
bot = None

# Security configurations
app.config.update(
    SECRET_KEY=os.getenv('FLASK_SECRET_KEY', os.urandom(24)),
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'True').lower() == 'true',
    SESSION_COOKIE_HTTPONLY=os.getenv('SESSION_COOKIE_HTTPONLY', 'True').lower() == 'true',
    SESSION_COOKIE_SAMESITE=os.getenv('SESSION_COOKIE_SAMESITE', 'Lax'),
    PERMANENT_SESSION_LIFETIME=3600,  # 1 hour
    SESSION_TYPE='filesystem'
)

# Initialize session
Session(app)

# Environment variables for Twitch OAuth
CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
REDIRECT_URI = os.getenv("CHATBOT_REDIRECT_URI", "http://localhost:5001/auth/twitch/callback")

if not all([CLIENT_ID, CLIENT_SECRET]):
    logger.error("Missing required environment variables: TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET")
    raise ValueError("Missing required environment variables")

# Authentication decorator
def require_twitch_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/auth/twitch')
        if session.get('username') != 'hunter_hues':
            return "Unauthorized", 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/wake')
def wake():
    try:
        # Always return 200 status code to indicate service is up, even during startup
        if not bot or not bot.is_ready:
            return jsonify({
                "status": "starting",
                "message": "Bot is initializing",
                "ready": False
            }), 200
        
        # Check if we can access the database
        db_session = SessionLocal()
        try:
            # Simple query to test database connection using text()
            db_session.execute(text("SELECT 1"))
            db_session.close()
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return jsonify({
                "status": "error",
                "message": "Database connection error",
                "ready": False
            }), 200  # Still return 200 to indicate service is up
        
        return jsonify({
            "status": "awake",
            "ready": bot.is_ready,
            "connected_channels": bot.connected_channels,
            "active_giveaways": len(bot.giveaways)
        }), 200
    except Exception as e:
        logger.error(f"Error in wake endpoint: {e}")
        return jsonify({
            "status": "error",
            "message": "Internal server error",
            "ready": False
        }), 200  # Return 200 even on errors so we know the service is running

@app.route('/')
def home():
    # If there's a return_to parameter, store it in session
    return_to = request.args.get('return_to')
    if return_to:
        session['return_to'] = return_to
        logger.info(f"Stored return_to URL: {return_to}")
    
    # If user is already authenticated and is hunter_hues, show the status page
    if 'user_id' in session and session.get('username') == 'hunter_hues':
        return status_page()
    
    # Otherwise, redirect to Twitch auth
    return redirect('/auth/twitch')

@app.route('/status_page')
@require_twitch_auth
def status_page():
    # Get return_to parameter from query string or session
    return_to = request.args.get('return_to')
    if not return_to and 'return_to' in session:
        return_to = session.get('return_to')
    
    # Default to main app URL if no return_to is provided
    main_app_url = os.getenv('MAIN_APP_URL', 'https://rafflebot-site.onrender.com')
    if not return_to:
        return_to = main_app_url
    
    return render_template('chatbot_status.html', main_app_url=return_to)

@app.route('/status')
def status_api():
    try:
        db_session = SessionLocal()
        active_giveaways = []
        
        # Get all active giveaways from the database
        active_records = db_session.query(ActiveGiveaway).all()
        
        for active in active_records:
            giveaway = db_session.query(Giveaway).filter_by(id=active.giveaway_id).first()
            if giveaway:
                # Get entries count from the bot's state if available
                entries_count = 0
                if bot and active.giveaway_id in bot.giveaways:
                    entries_count = len(bot.giveaways[active.giveaway_id]['entries'])
                
                active_giveaways.append({
                    'id': giveaway.id,
                    'title': giveaway.title,
                    'channel_name': active.channel_name,
                    'frequency': giveaway.frequency,
                    'threshold': giveaway.threshold,
                    'entries_count': entries_count,
                    'last_updated': datetime.utcnow().isoformat()
                })
        
        return jsonify({
            'running': True,
            'active_giveaways': active_giveaways
        })
    except Exception as e:
        return jsonify({
            'running': False,
            'error': str(e)
        })
    finally:
        db_session.close()

@app.route('/auth/twitch')
def auth_twitch():
    # Store the return_to parameter in the session 
    return_to = request.args.get('return_to')
    if return_to:
        session['return_to'] = return_to
        logger.info(f"Stored return_to in session for auth flow: {return_to}")
    elif 'return_to' in session:
        logger.info(f"Using existing return_to from session: {session['return_to']}")
    
    logger.info(f"AUTH TWITCH: Using REDIRECT_URI: {REDIRECT_URI}")
    return redirect(
        f"https://id.twitch.tv/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=user:read:email"
    )

@app.route('/auth/twitch/callback')
def auth_twitch_callback():
    code = request.args.get("code")
    if not code:
        return "Authorization failed: missing code", 400

    try:
        token_response = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
        )
        token_response.raise_for_status()
        token_data = token_response.json()

        if "access_token" not in token_data:
            logger.error("Twitch API response missing access_token.")
            return "Authorization failed: missing access token", 400

    except requests.exceptions.RequestException as e:
        logger.error(f"Twitch API error during token exchange: {e}")
        return "Authorization failed due to Twitch API error", 400

    try:
        user_response = requests.get(
            "https://api.twitch.tv/helix/users",
            headers={
                "Authorization": f"Bearer {token_data['access_token']}",
                "Client-Id": CLIENT_ID
            }
        )
        user_response.raise_for_status()
        user_data = user_response.json()

        if "data" not in user_data or not user_data["data"]:
            logger.error("Twitch user data is missing or empty.")
            return "Authorization failed: unable to fetch user data", 400

        user_info = user_data["data"][0]
        
        # If not admin user, redirect back to main app
        if user_info["display_name"].lower() != "hunter_hues":
            # Use environment variable with fallback to the known URL
            app_url = os.getenv('MAIN_APP_URL', 'https://rafflebot-site.onrender.com')
            
            # Get the return_to from session if available, otherwise use default URL
            redirect_url = session.get('return_to', app_url)
                
            logger.info(f"Non-admin user redirecting to: {redirect_url}")
            
            # Check if template exists and render it
            try:
                template_path = os.path.join('templates', 'redirect.html')
                if not os.path.exists(template_path):
                    logger.error(f"Template file does not exist: {template_path}")
                    # Fall back to direct redirect if template is missing
                    return redirect(redirect_url)
                
                # Use the redirect template to explain what's happening
                logger.info(f"Rendering redirect.html template with redirect_url={redirect_url}")
                return render_template('redirect.html', redirect_url=redirect_url)
            except Exception as e:
                logger.error(f"Error rendering template: {str(e)}")
                # Fall back to direct redirect
                return redirect(redirect_url)

        # Admin user (hunter_hues)
        session["user_id"] = user_info["id"]
        session["username"] = user_info["display_name"]
        logger.info(f"Admin user {user_info['display_name']} authenticated")

    except requests.exceptions.RequestException as e:
        logger.error(f"Twitch API error while fetching user data: {e}")
        return "Authorization failed due to Twitch API error", 400
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return "Authorization failed due to an unexpected error", 400

    return redirect("/")

# Environment variables
BOT_NICK = os.getenv("BOT_NICK", "rafflebot_giveaways")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
CHANNEL = os.getenv("DEFAULT_CHANNEL", "rafflebot_giveaways")

if not BOT_TOKEN:
    logger.error("Missing required environment variable: BOT_TOKEN")
    raise ValueError("Missing required environment variable: BOT_TOKEN")

lock = threading.Lock()

def is_giveaway_owner(ctx, giveaway):
    db_session = SessionLocal()
    user = db_session.query(User).filter_by(username=ctx.author.name).first()
    db_session.close()
    return user and user.id == giveaway.creator_id

class Bot(commands.Bot):
    def __init__(self):
        # Initialize with an empty list of channels - we'll join them as needed
        super().__init__(token=BOT_TOKEN, prefix=BOT_PREFIX, initial_channels=[])
        self._connected_channels = set()  # Use a set for better performance
        self._nick = BOT_NICK 
        self.logger = logging.getLogger("Bot")
        self.giveaways = {}  # Dictionary to store giveaway states
        self.heartbeat_task = None
        self.process_id = os.getpid()
        self.is_ready = False
        self.logger.info("Bot initialized")

    @property
    def connected_channels(self):
        return list(self._connected_channels)

    @connected_channels.setter
    def connected_channels(self, channels):
        self._connected_channels = set(channels)

    @property
    def nick(self):
        return self._nick

    @nick.setter
    def nick(self, value):
        self._nick = value

    async def update_heartbeat(self):
        while True:
            try:
                db_session = SessionLocal()
                # Update heartbeat for all active giveaways
                for giveaway_id in self.giveaways.keys():
                    tracker = db_session.query(ProcessTracker).filter_by(
                        process_id=self.process_id,
                        giveaway_id=giveaway_id
                    ).first()
                    
                    if tracker:
                        tracker.last_heartbeat = datetime.utcnow()
                        db_session.commit()
                db_session.close()
            except Exception as e:
                self.logger.error(f"Error updating heartbeat: {e}")
            await asyncio.sleep(30)  # Update every 30 seconds

    async def event_ready(self):
        self.logger.info(f"Bot is ready! Logged in as {self.nick}")
        self.is_ready = True
        
        # Start heartbeat task
        self.heartbeat_task = asyncio.create_task(self.update_heartbeat())
        self.logger.info("Heartbeat task started")
        
        # Start polling for giveaways
        asyncio.create_task(self.poll_giveaways())
        self.logger.info("Giveaway polling task started")

    async def join_channel_with_retry(self, channel_name, max_retries=3):
        channel_name = channel_name.lower()
        
        # If we're already in the channel, return True
        if channel_name in self._connected_channels:
            self.logger.info(f"Already in channel {channel_name}")
            return True

        for attempt in range(max_retries):
            try:
                self.logger.info(f"Attempting to join channel {channel_name} (attempt {attempt + 1}/{max_retries})")
                
                # Join the channel
                await self.join_channels([channel_name])
                
                # Wait for join to complete
                await asyncio.sleep(2)
                
                # Verify we're in the channel
                if channel_name in self._connected_channels:
                    self.logger.info(f"Successfully joined channel: {channel_name}")
                    return True
                else:
                    self.logger.warning(f"Channel {channel_name} not in connected_channels after join attempt")
                    
                    # Try to force a reconnection
                    if attempt < max_retries - 1:
                        self.logger.info(f"Attempting to reconnect to Twitch...")
                        await self.close()
                        await asyncio.sleep(2)
                        await self.connect()
                        await asyncio.sleep(2)
                
            except Exception as e:
                self.logger.error(f"Error joining channel {channel_name} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))  # Exponential backoff
        
        return False

    async def event_join(self, channel, user):
        if user.name.lower() == self._nick.lower():
            self.logger.info(f"Successfully joined channel: {channel.name}")
            self._connected_channels.add(channel.name.lower())

    async def event_part(self, channel, user):
        if user.name.lower() == self._nick.lower():
            self.logger.info(f"Left channel: {channel.name}")
            self._connected_channels.discard(channel.name.lower())

    async def poll_giveaways(self):
        self.logger.info("Starting to poll for giveaways")
        while True:
            try:
                if not self.is_ready:
                    self.logger.info("Bot not ready yet, waiting...")
                    await asyncio.sleep(5)
                    continue

                db_session = SessionLocal()
                
                # Get all active giveaways
                active_giveaways = db_session.query(ActiveGiveaway).all()
                self.logger.info(f"Found {len(active_giveaways)} active giveaways")
                
                # Update our giveaways dictionary
                current_giveaways = set(self.giveaways.keys())
                new_giveaways = set()
                
                for active in active_giveaways:
                    new_giveaways.add(active.giveaway_id)
                    if active.giveaway_id not in self.giveaways:
                        # New giveaway found, start managing it
                        giveaway = db_session.query(Giveaway).filter_by(id=active.giveaway_id).first()
                        if giveaway:
                            self.logger.info(f"Starting new giveaway: {giveaway.title}")
                            self.giveaways[active.giveaway_id] = {
                                'giveaway': giveaway,
                                'entries': [],
                                'empty_rounds': 0,
                                'task': None,
                                'channel_name': active.channel_name.lower()
                            }
                            # Start managing this giveaway
                            self.giveaways[active.giveaway_id]['task'] = asyncio.create_task(
                                self.manage_giveaway(active.giveaway_id)
                            )
                
                # Stop managing giveaways that are no longer active
                for giveaway_id in current_giveaways - new_giveaways:
                    if giveaway_id in self.giveaways:
                        self.logger.info(f"Stopping giveaway {giveaway_id}")
                        if self.giveaways[giveaway_id]['task']:
                            self.giveaways[giveaway_id]['task'].cancel()
                        del self.giveaways[giveaway_id]
                
                db_session.close()
            except Exception as e:
                self.logger.error(f"Error polling giveaways: {e}")
            
            await asyncio.sleep(10)  # Poll every 10 seconds

    async def manage_giveaway(self, giveaway_id):
        try:
            giveaway_data = self.giveaways[giveaway_id]
            giveaway = giveaway_data['giveaway']
            self.logger.info(f"Managing giveaway: {giveaway.title}")
            
            db_session = SessionLocal()
            items = db_session.query(Item).filter_by(giveaway_id=giveaway.id, is_won=False).all()
            
            if not items:
                self.logger.info(f"No items found for giveaway '{giveaway.title}'")
                return
            
            # Ensure we're in the channel
            channel_name = giveaway_data['channel_name'].lower()
            if not await self.join_channel_with_retry(channel_name):
                self.logger.error(f"Failed to join channel {channel_name} after multiple attempts")
                return
            
            channel = self.get_channel(channel_name)
            if not channel:
                self.logger.error(f"Could not find channel {channel_name}")
                return

            self.logger.info(f"Starting giveaway loop for {giveaway.title}")
            while True:  # Loop until giveaway ends
                # Refresh items list to get latest won status
                items = db_session.query(Item).filter_by(giveaway_id=giveaway.id, is_won=False).all()
                
                if not items:
                    self.logger.info(f"All items won in giveaway '{giveaway.title}'")
                    await channel.send(f"All items in giveaway '{giveaway.title}' have been won!")
                    break
                
                for item in items:
                    try:
                        # Announce the item
                        self.logger.info(f"Announcing item: {item.name}")
                        await channel.send(f"Giving away: {item.name}!")
                        
                        # Wait for entries
                        await asyncio.sleep(giveaway.frequency)
                        
                        # Select winner if there are entries
                        if giveaway_data['entries']:
                            winner = random.choice(giveaway_data['entries'])
                            item.is_won = True
                            item.winner_username = winner
                            db_session.commit()
                            
                            self.logger.info(f"Winner selected: {winner} for item {item.name}")
                            await channel.send(f"Congratulations {winner}! You've won {item.name}!")
                            
                            giveaway_data['entries'].clear()  # Clear entries after a win
                            giveaway_data['empty_rounds'] = 0  # Reset empty rounds counter
                        else:
                            giveaway_data['empty_rounds'] += 1
                            self.logger.info(f"No entries for {item.name}. Empty rounds: {giveaway_data['empty_rounds']}/{giveaway.threshold}")
                            
                            if giveaway_data['empty_rounds'] >= giveaway.threshold:
                                await channel.send(
                                    f"No entries for {giveaway_data['empty_rounds']} consecutive rounds. "
                                    f"Giveaway '{giveaway.title}' has been automatically ended."
                                )
                                # Clean up the giveaway in the database
                                active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway_id).first()
                                if active_giveaway:
                                    db_session.delete(active_giveaway)
                                    db_session.commit()
                                return  # End the giveaway
                            else:
                                await channel.send(
                                    f"No entries for {item.name}. It will be re-given in the next round. "
                                    f"({giveaway_data['empty_rounds']}/{giveaway.threshold} empty rounds)"
                                )
                    
                    except Exception as e:
                        self.logger.error(f"Error processing item '{item.name}': {e}")
            
        except Exception as e:
            self.logger.error(f"Error managing giveaway {giveaway_id}: {e}")
        finally:
            try:
                # Clean up the giveaway in the database
                active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway_id).first()
                if active_giveaway:
                    db_session.delete(active_giveaway)
                    db_session.commit()
            except Exception as e:
                self.logger.error(f"Error cleaning up giveaway {giveaway_id}: {e}")
            finally:
                db_session.close()
                # Clean up when giveaway ends
                if giveaway_id in self.giveaways:
                    del self.giveaways[giveaway_id]
                self.logger.info(f"Giveaway {giveaway_id} cleanup completed")

    @commands.command(name="enter")
    async def enter_giveaway(self, ctx):
        channel_name = ctx.channel.name
        for giveaway_id, data in self.giveaways.items():
            if data['channel_name'] == channel_name:
                if ctx.author.name not in data['entries']:
                    data['entries'].append(ctx.author.name)
                    await ctx.send(f"{ctx.author.name}, you have been entered into the giveaway!")
                else:
                    await ctx.send(f"{ctx.author.name}, you are already entered!")
                return
        
        await ctx.send("There is no active giveaway in this channel.")

    async def event_message(self, message):
        if message.author is None:
            return

        self.logger.debug(f"{message.author.name}: {message.content}")

        if message.author.name.lower() == self.nick.lower():
            return

        await self.handle_commands(message)

    @commands.command(name="startgiveaway")
    async def start_giveaway(self, ctx, identifier: str = None):
        if self.giveaways.get(int(identifier)):
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

        self.giveaways[int(identifier)] = {
            'giveaway': giveaway,
            'entries': [],
            'empty_rounds': 0,
            'task': None
        }
        self.giveaways[int(identifier)]['task'] = asyncio.create_task(self.manage_giveaway(int(identifier)))
        print(f"Starting giveaway: {giveaway.title} with threshold: {giveaway.threshold}")
        await ctx.send(f"A giveaway has started: {giveaway.title}! Type !enter to participate.")

    @commands.command(name="endgiveaway")
    async def end_giveaway(self, ctx):
        if not self.giveaways:
            await ctx.send("There are no active giveaways to end.")
            return

        for giveaway_id, data in self.giveaways.items():
            if data['task']:
                data['task'].cancel()
                print(f"Giveaway {giveaway_id} task canceled.")
                try:
                    await data['task']
                except asyncio.CancelledError:
                    print(f"Giveaway {giveaway_id} task cleanup completed.")

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

    async def shutdown(self):
        self.logger.info("Initiating bot shutdown...")
        try:
            db_session = SessionLocal()
            # Mark process tracker as inactive
            for giveaway_id in self.giveaways.keys():
                tracker = db_session.query(ProcessTracker).filter_by(
                    process_id=self.process_id,
                    giveaway_id=giveaway_id
                ).first()
                if tracker:
                    tracker.is_active = False
                    db_session.commit()
                    self.logger.info(f"Marked process tracker {self.process_id} as inactive for giveaway {giveaway_id}")

            # Remove active giveaway records
            for giveaway_id in self.giveaways.keys():
                active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway_id).first()
                if active_giveaway:
                    db_session.delete(active_giveaway)
                    db_session.commit()
                    self.logger.info(f"Removed active giveaway record for giveaway {giveaway_id}")
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
            
            # Close the bot connection instead of calling super().shutdown()
            self.logger.info("Closing bot connection...")
            await self.close()
            self.logger.info("Bot shutdown complete")


if __name__ == "__main__":
    # Start the Flask server
    logger.info("Starting Flask server...")
    threading.Thread(target=serve, args=(app,), kwargs={
        'host': '0.0.0.0',
        'port': 5001,
        'threads': 4
    }, daemon=True).start()
    logger.info("Flask server started")
    
    # Create and run the bot
    logger.info("Creating bot instance...")
    bot = Bot()
    try:
        logger.info("Starting bot...")
        bot.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        asyncio.create_task(bot.shutdown())
    except Exception as e:
        logger.error(f"Error: {e}")
        asyncio.create_task(bot.shutdown())
