import os
import sys
import time
import logging
import psutil
import subprocess
from dotenv import load_dotenv
from models import SessionLocal, ActiveGiveaway, ProcessTracker

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def cleanup_stale_processes():
    """Clean up stale processes and process trackers."""
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

def start_giveaway(giveaway_id, channel_name):
    """Start a giveaway by launching the chatbot process."""
    try:
        db_session = SessionLocal()
        
        # Check if giveaway is already running
        active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway_id).first()
        if active_giveaway:
            # Check if process is still running
            if psutil.pid_exists(active_giveaway.process_id):
                logger.info(f"Giveaway {giveaway_id} is already running with process {active_giveaway.process_id}")
                db_session.close()
                return False
            else:
                # Clean up stale entry
                db_session.delete(active_giveaway)
                db_session.commit()
        
        # Start the chatbot process
        process = subprocess.Popen(["python", "chatbot.py", str(giveaway_id), channel_name])
        
        # Record the active giveaway
        active_giveaway = ActiveGiveaway(
            giveaway_id=giveaway_id,
            process_id=process.pid,
            channel_name=channel_name
        )
        db_session.add(active_giveaway)
        
        # Create process tracker
        tracker = ProcessTracker(
            process_id=process.pid,
            giveaway_id=giveaway_id
        )
        db_session.add(tracker)
        
        db_session.commit()
        db_session.close()
        
        logger.info(f"Started giveaway {giveaway_id} with process {process.pid}")
        return True
    except Exception as e:
        logger.error(f"Error starting giveaway {giveaway_id}: {e}")
        try:
            db_session.close()
        except:
            pass
        return False

def stop_giveaway(giveaway_id):
    """Stop a giveaway by terminating the chatbot process."""
    try:
        db_session = SessionLocal()
        
        # Check if giveaway exists and is running
        active_giveaway = db_session.query(ActiveGiveaway).filter_by(giveaway_id=giveaway_id).first()
        if not active_giveaway:
            logger.info(f"Giveaway {giveaway_id} is not running")
            db_session.close()
            return False
        
        # Try to terminate the process
        try:
            process = psutil.Process(active_giveaway.process_id)
            logger.info(f"Terminating process {active_giveaway.process_id} for giveaway {giveaway_id}")
            process.terminate()
            process.wait(timeout=5)  # Wait up to 5 seconds for the process to terminate
            logger.info(f"Process {active_giveaway.process_id} terminated successfully")
        except psutil.NoSuchProcess:
            logger.info(f"Process {active_giveaway.process_id} already terminated")
        except psutil.TimeoutExpired:
            logger.warning(f"Process {active_giveaway.process_id} did not terminate in time, forcing kill")
            process.kill()  # Force kill if it doesn't terminate
            try:
                process.wait(timeout=2)  # Wait a bit more for the process to be killed
            except psutil.TimeoutExpired:
                logger.error(f"Failed to kill process {active_giveaway.process_id}")
        
        # Clean up any process trackers for this giveaway
        trackers = db_session.query(ProcessTracker).filter_by(
            giveaway_id=giveaway_id,
            is_active=True
        ).all()
        
        for tracker in trackers:
            try:
                # Check if the process is still running
                process = psutil.Process(tracker.process_id)
                if process.is_running():
                    logger.info(f"Terminating tracked process {tracker.process_id}")
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        process.kill()
            except psutil.NoSuchProcess:
                pass  # Process already terminated
            
            # Mark tracker as inactive
            tracker.is_active = False
        
        # Remove the active giveaway record
        db_session.delete(active_giveaway)
        db_session.commit()
        logger.info(f"Giveaway {giveaway_id} stopped successfully")
        
        db_session.close()
        return True
    except Exception as e:
        logger.error(f"Error stopping giveaway {giveaway_id}: {e}")
        try:
            db_session.close()
        except:
            pass
        return False

def main():
    """Main function to run the worker."""
    logger.info("Starting giveaway bot worker")
    
    # Clean up any stale processes on startup
    cleanup_stale_processes()
    
    # Main loop
    while True:
        try:
            db_session = SessionLocal()
            
            # Check for giveaways that should be stopped
            giveaways_to_stop = db_session.query(ActiveGiveaway).filter_by(should_stop=True).all()
            for active in giveaways_to_stop:
                logger.info(f"Found giveaway {active.giveaway_id} marked for stopping")
                stop_giveaway(active.giveaway_id)
            
            # Check for giveaways that need to be started
            giveaways_to_start = db_session.query(ActiveGiveaway).filter(
                ActiveGiveaway.process_id == 0,
                ActiveGiveaway.should_stop == False
            ).all()
            for active in giveaways_to_start:
                logger.info(f"Found giveaway {active.giveaway_id} to start")
                start_giveaway(active.giveaway_id, active.channel_name)
            
            db_session.close()
            
            # Clean up stale processes every 5 minutes
            cleanup_stale_processes()
            
            # Sleep for 5 minutes
            time.sleep(300)
        except KeyboardInterrupt:
            logger.info("Worker shutting down")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(60)  # Sleep for 1 minute before retrying

if __name__ == "__main__":
    main() 