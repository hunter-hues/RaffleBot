from flask import Flask, redirect, request, session, render_template
import requests
import os
import subprocess
from dotenv import load_dotenv
from models import SessionLocal, User, Giveaway, Item, Winner, ActiveGiveaway, ProcessTracker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
import psutil
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_session import Session
import logging
from logging.handlers import RotatingFileHandler
import re
import threading
import time
from functools import wraps
import urllib.parse

# Load environment variables only in development
if os.getenv('FLASK_ENV') != 'production':
    load_dotenv()

# Configure logging
def setup_logging():
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    log_file = os.getenv('LOG_FILE', 'app.log')
    
    # Create a formatter that strips ANSI color codes
    class NoColorFormatter(logging.Formatter):
        def format(self, record):
            # Remove ANSI color codes
            message = super().format(record)
            message = re.sub(r'\x1b\[[0-9;]*m', '', message)
            return message
    
    # Create handlers
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10000000,
        backupCount=5,
        encoding='utf-8'
    )
    console_handler = logging.StreamHandler()
    
    # Set formatters
    formatter = NoColorFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Configure werkzeug logger
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.INFO)
    werkzeug_logger.addHandler(file_handler)
    werkzeug_logger.addHandler(console_handler)
    
    return logging.getLogger(__name__)

logger = setup_logging()

app = Flask(__name__)

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

# Configure rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri="memory://",
    default_limits=[os.getenv('RATE_LIMIT_DEFAULT', '10/minute')],
    strategy="fixed-window"
)

# Add specific rate limits for auth routes
@limiter.limit(os.getenv('RATE_LIMIT_AUTH', '5/minute'))
@app.route("/auth/twitch")
def auth_twitch():
    logger.info(f"WEB APP AUTH_TWITCH: Using REDIRECT_URI: {REDIRECT_URI}")
    return redirect(
        f"https://id.twitch.tv/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=user:read:email"
    )

# Security headers middleware
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# Error handlers
@app.errorhandler(429)
def ratelimit_handler(e):
    logger.warning(f"Rate limit exceeded for {request.remote_addr}")
    return "Rate limit exceeded", 429

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {str(e)}")
    return "Internal server error", 500

@app.errorhandler(404)
def not_found_error(e):
    logger.warning(f"Page not found: {request.url}")
    return "Page not found", 404

# Environment variables
CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/auth/twitch/callback")

if not all([CLIENT_ID, CLIENT_SECRET]):
    logger.error("Missing required environment variables: TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET")
    raise ValueError("Missing required environment variables")

chatbot_processes = {}

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/auth/twitch/callback")
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
            app.logger.error("Twitch API response missing access_token.")
            return "Authorization failed: missing access token", 400

    except requests.exceptions.RequestException as e:
        app.logger.error(f"Twitch API error during token exchange: {e}")
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
            app.logger.error("Twitch user data is missing or empty.")
            return "Authorization failed: unable to fetch user data", 400

        user_info = user_data["data"][0]

        db_session = SessionLocal()
        user = db_session.query(User).filter_by(twitch_id=user_info["id"]).first()
        if not user:
            import re
            if not re.match(r"^[a-zA-Z0-9_]+$", user_info["display_name"]):
                db_session.close()
                raise ValueError("Invalid username")
            
            user = User(
                twitch_id=user_info["id"],
                username=user_info["display_name"],
                channel_name=user_info["display_name"].lower()
            )
            db_session.add(user)
            db_session.commit()
        else:
            user.channel_name = user_info["display_name"].lower()
            db_session.commit()

        session["user_id"] = user.id
        session["username"] = user_info["display_name"]
        db_session.close()

    except requests.exceptions.RequestException as e:
        app.logger.error(f"Twitch API error while fetching user data: {e}")
        return "Authorization failed due to Twitch API error", 400
    except Exception as e:
        app.logger.error(f"Unexpected error: {e}")
        return "Authorization failed due to an unexpected error", 400

    return redirect("/dashboard")

def ensure_chatbot_running(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        chatbot_url = os.getenv('CHATBOT_URL', 'http://localhost:5001')
        
        # Store the requested URL to redirect back after chatbot wakes up
        current_url = request.url
        
        # URL encode the current URL to prevent issues with special characters
        encoded_url = urllib.parse.quote(current_url)
        
        # We redirect to the chatbot service to wake it up.
        # The Flask server at the chatbot will start immediately, but the Twitch bot
        # might take a few moments to fully initialize and connect to Twitch.
        # The chatbot will check if the bot is ready and redirect back once it's ready.
        wake_redirect_url = f"{chatbot_url}/wake_redirect?return_to={encoded_url}"
        logger.info(f"Redirecting to wake up Twitch bot: {wake_redirect_url}")
        
        return redirect(wake_redirect_url)
        
    return decorated_function

@app.route("/dashboard")
@ensure_chatbot_running
def dashboard():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")

    db_session = SessionLocal()
    try:
        # Get user's giveaways
        giveaways = db_session.query(Giveaway).filter_by(creator_id=user_id).all()
        
        # For each giveaway, check if it's running by querying the chatbot's status endpoint
        chatbot_url = os.getenv('CHATBOT_URL', 'http://localhost:5001')
        for giveaway in giveaways:
            active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway.id).first()
            if active_giveaway:
                try:
                    # Try to get status from the chatbot
                    response = requests.get(f'{chatbot_url}/status', timeout=1)
                    if response.status_code == 200:
                        status_data = response.json()
                        # Check if this giveaway is in the active giveaways list
                        giveaway.is_running = any(
                            ag['id'] == giveaway.id 
                            for ag in status_data.get('active_giveaways', [])
                        )
                    else:
                        giveaway.is_running = False
                except requests.RequestException:
                    # If we can't reach the chatbot, check if the process is still running
                    try:
                        process = psutil.Process(active_giveaway.process_id)
                        giveaway.is_running = process.is_running()
                    except psutil.NoSuchProcess:
                        giveaway.is_running = False
                        # Clean up stale active giveaway
                        db_session.delete(active_giveaway)
                        db_session.commit()
            else:
                giveaway.is_running = False

        return render_template("dashboard.html", giveaways=giveaways)
    finally:
        db_session.close()


@app.route("/giveaway/create", methods=["GET", "POST"])
def create_giveaway():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        frequency = request.form.get("frequency", "").strip()
        threshold = request.form.get("threshold", "").strip()

        try:
            if not title:
                raise ValueError("Title is required.")
            if not frequency.isdigit() or not threshold.isdigit():
                raise ValueError("Frequency and threshold must be valid numbers.")
            frequency = int(frequency)
            threshold = int(threshold)
            if frequency <= 0 or threshold < 0 or frequency > 1_000_000:
                raise ValueError("Frequency or threshold out of valid range.")
        except ValueError as e:
            return f"Invalid input: {str(e)}", 400

        if not re.match(r"^[a-zA-Z0-9\s]+$", title):
            return {"error": "Invalid title detected. Only letters, numbers, and spaces are allowed."}, 400
        if len(title) > 255:
            return {"error": "Title exceeds the maximum length of 255 characters."}, 400
        db_session = SessionLocal()
        user = db_session.query(User).filter_by(id=user_id).first()
        if not user:
            db_session.close()
            return "User not found.", 403
        if not re.match(r"^[a-zA-Z0-9\s]+$", title):
            db_session.close()
            return {"error": "Invalid title detected. Only letters, numbers, and spaces are allowed."}, 400

        giveaway = Giveaway(
            title=title,
            frequency=frequency,
            threshold=threshold,
            creator_id=user.id
        )
        db_session.add(giveaway)
        db_session.commit()
        db_session.close()


        return redirect("/dashboard")

    return render_template("create_giveaway.html")

@app.route("/giveaways")
def list_giveaways():
    user_id = session.get("user_id")
    db_session = SessionLocal()
    giveaways = db_session.query(Giveaway).filter_by(creator_id=user_id).all()
    db_session.close()

    return "<br>".join([f"ID: {g.id}, Title: {g.title}" for g in giveaways])

@app.route("/giveaway/delete/<int:id>", methods=["POST", "GET"])
def delete_giveaway(id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")

    db_session = SessionLocal()
    if not isinstance(user_id, int) or user_id <= 0:
        db_session.close()
        return "Invalid user ID.", 400

    giveaway = db_session.query(Giveaway).filter_by(id=id, creator_id=user_id).first()
    if not giveaway:
        db_session.close()
        return "Giveaway not found or you do not have permission to delete it.", 403

    non_won_items = db_session.query(Item).filter_by(giveaway_id=id, is_won=False).all()
    for item in non_won_items:
        db_session.delete(item)

    won_items = db_session.query(Item).filter_by(giveaway_id=id, is_won=True).all()
    for won_item in won_items:
        print(f"Retained won item: {won_item.name} (ID: {won_item.id})")

    db_session.delete(giveaway)

    try:
        db_session.commit()
    except IntegrityError as e:
        db_session.rollback()
        print(f"Error during giveaway deletion: {str(e)}")
        return "Failed to delete giveaway due to database constraints.", 500
    finally:
        db_session.close()

    return redirect("/dashboard")

@app.route("/giveaway/start/<int:giveaway_id>")
@ensure_chatbot_running
def start_giveaway(giveaway_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")

    db_session = SessionLocal()
    try:
        # Check if giveaway exists and user has permission
        giveaway = db_session.query(Giveaway).filter_by(id=giveaway_id, creator_id=user_id).first()
        if not giveaway:
            return "Giveaway not found or you don't have permission to start it.", 403

        # Check if giveaway is already running
        active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway_id).first()
        if active_giveaway:
                return "This giveaway is already running.", 400

        # Get the creator's username to use as the channel name
        creator = db_session.query(User).filter_by(id=giveaway.creator_id).first()
        if not creator:
            return "Creator information is incomplete.", 400
        
        channel_name = creator.channel_name or creator.username.lower()

        # Check if chatbot is running - use environment variable for URL
        chatbot_url = os.getenv('CHATBOT_URL', 'http://localhost:5001')
        try:
            response = requests.get(f'{chatbot_url}/wake', timeout=1)
            if response.status_code != 200:
                return "Chatbot is not running. Please start chatbot.py first.", 503
        except requests.RequestException:
            return "Chatbot is not running. Please start chatbot.py first.", 503
        
        # Record the active giveaway
        active_giveaway = ActiveGiveaway(
            giveaway_id=giveaway_id,
            process_id=os.getpid(),  # Use the chatbot's process ID
            channel_name=channel_name
        )
        db_session.add(active_giveaway)
        db_session.commit()

        return redirect("/dashboard")

    except Exception as e:
        db_session.rollback()
        logger.error(f"Error starting giveaway: {str(e)}")
        return f"Failed to start giveaway: {str(e)}", 500
    finally:
        db_session.close()

@app.route("/giveaway/edit/<int:id>", methods=["GET", "POST"])
def edit_giveaway(id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")

    db_session = SessionLocal()
    giveaway = db_session.query(Giveaway).options(joinedload(Giveaway.items)).filter_by(id=id).first()
    if not giveaway:
        db_session.close()
        return "Giveaway not found.", 404

    if giveaway.creator_id != user_id:
        db_session.close()
        return "Unauthorized to edit this giveaway.", 403

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        frequency = request.form.get("frequency", "").strip()
        threshold = request.form.get("threshold", "").strip()
        if not re.match(r"^[a-zA-Z0-9\s]+$", title):
            db_session.close()
            return "Invalid title. Only letters, numbers, and spaces are allowed.", 400
        if not frequency.isdigit() or not threshold.isdigit():
            db_session.close()
            return "Frequency and threshold must be valid numbers.", 400

        giveaway.title = title
        giveaway.frequency = int(frequency)
        giveaway.threshold = int(threshold)
        db_session.commit()
        db_session.close()
        return redirect("/dashboard")

    db_session.close()
    return render_template("edit_giveaway.html", giveaway=giveaway)

@app.route("/giveaway/add-item/<int:giveaway_id>", methods=["POST"])
def add_item(giveaway_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")

    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip()

    if not name:
        return "Item name is required.", 400
    if not code:
        return "Item code is required.", 400

    db_session = SessionLocal()
    giveaway = db_session.query(Giveaway).filter_by(id=giveaway_id).first()
    if not giveaway:
        db_session.close()
        return "Giveaway not found.", 404

    if not re.match(r"^[a-zA-Z0-9\s]+$", name):
        db_session.close()
        return "Invalid item name. Only letters, numbers, and spaces are allowed.", 400
    if not re.match(r"^[a-zA-Z0-9]+$", code):
        db_session.close()
        return "Invalid item code. Only alphanumeric characters are allowed.", 400

    item = Item(name=name, code=code, giveaway_id=giveaway_id)
    db_session.add(item)
    db_session.commit()
    db_session.close()


    return redirect(f"/giveaway/edit/{giveaway_id}")

@app.route("/giveaway/remove-item/<int:item_id>", methods=["POST"])
def remove_item(item_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")

    db_session = SessionLocal()
    try:
        item = db_session.query(Item).join(Giveaway, Giveaway.id == Item.giveaway_id).filter(
            Item.id == item_id,
            Giveaway.creator_id == user_id
        ).first()

        print(f"Queried item: {item}")


        if not item:
            return "Item not found or permission denied.", 403

        giveaway_id = item.giveaway_id
        db_session.delete(item)
        db_session.commit()

        return redirect(f"/giveaway/edit/{giveaway_id}")
    except Exception as e:
        print(f"Error removing item: {e}")
        return "An error occurred while trying to remove the item.", 500
    finally:
        db_session.close()


@app.route("/giveaway/stop/<int:giveaway_id>")
def stop_giveaway(giveaway_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/auth/twitch")

    db_session = SessionLocal()
    try:
        # Check if giveaway exists and user has permission
        giveaway = db_session.query(Giveaway).filter_by(id=giveaway_id, creator_id=user_id).first()
        if not giveaway:
            return "Giveaway not found or you don't have permission to stop it.", 403

        # Check if giveaway is running
        active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway_id).first()
        if not active_giveaway:
            return "This giveaway is not running.", 400
        
        # Remove the active giveaway record
        db_session.delete(active_giveaway)
        db_session.commit()
        logger.info(f"Giveaway {giveaway_id} stopped successfully")

        return redirect("/dashboard")

    except Exception as e:
        db_session.rollback()
        logger.error(f"Error stopping giveaway: {str(e)}")
        return f"Failed to stop giveaway: {str(e)}", 500
    finally:
        db_session.close()


@app.route("/winnings")
def winnings():
    user_username = session.get("username")
    if not user_username:
        return redirect("/auth/twitch")

    db_session = SessionLocal()
    if not re.match(r"^[a-zA-Z0-9_]+$", user_username):
        db_session.close()
        raise ValueError("Invalid username")

    winnings = (
        db_session.query(Item)
        .filter(Item.is_won == True, Item.winner_username == user_username)
        .all()
    )

    db_session.close()

    return render_template("winnings.html", winnings=winnings)

# Function to clean up stale processes
def cleanup_stale_processes():
    while True:
        try:
            db_session = SessionLocal()
            
            # Clean up stale process trackers
            stale_trackers = db_session.query(ProcessTracker).filter_by(is_active=True).all()
            for tracker in stale_trackers:
                try:
                    process = psutil.Process(tracker.process_id)
                    if not process.is_running():
                        logger.info(f"Marking stale process tracker {tracker.process_id} as inactive")
                        tracker.is_active = False
                except psutil.NoSuchProcess:
                    logger.info(f"Marking non-existent process tracker {tracker.process_id} as inactive")
                    tracker.is_active = False
            
            # Clean up stale active giveaways
            active_giveaways = db_session.query(ActiveGiveaway).all()
            for active in active_giveaways:
                try:
                    process = psutil.Process(active.process_id)
                    if not process.is_running():
                        logger.info(f"Removing stale active giveaway for process {active.process_id}")
                        db_session.delete(active)
                except psutil.NoSuchProcess:
                    logger.info(f"Removing active giveaway for non-existent process {active.process_id}")
                    db_session.delete(active)
            
            db_session.commit()
            db_session.close()
        except Exception as e:
            logger.error(f"Error in cleanup_stale_processes: {e}")
            try:
                db_session.close()
            except:
                pass
        
        # Sleep for 5 minutes before next cleanup
        time.sleep(300)

# Start the cleanup thread
cleanup_thread = threading.Thread(target=cleanup_stale_processes, daemon=True)
cleanup_thread.start()

if __name__ == "__main__":
    app.run(debug=True)