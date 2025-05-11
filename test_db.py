from sqlalchemy import text
from models import engine, SessionLocal, Base, User
import os
from dotenv import load_dotenv
import traceback
from pathlib import Path

def test_database_connection():
    """Test the database connection and print connection details."""
    try:
        # Get absolute path to .env.production
        env_path = Path('.env.production').absolute()
        print(f"\nLooking for .env.production at: {env_path}")
        
        # Load production environment
        print("Loading environment from .env.production...")
        if not env_path.exists():
            print(f"[ERROR] .env.production not found at {env_path}")
            return False
            
        load_dotenv(env_path)
        
        # Get database URL (masked for security)
        db_url = os.getenv('DATABASE_URL', '')
        if not db_url:
            print("[ERROR] DATABASE_URL not found in .env.production")
            return False
            
        # Debug: Print the parts of the URL (masked)
        print("\nDebug - URL parts:")
        parts = db_url.split('@')
        print(f"Number of parts after @ split: {len(parts)}")
        if len(parts) == 2:
            print(f"First part (masked): ***")
            print(f"Second part: {parts[1]}")
            
            # Parse the host part correctly
            host_part = parts[1]
            print(f"\nDebug - Host part: {host_part}")
            
            # Split by slash to separate host and database
            slash_parts = host_part.split('/')
            print(f"Parts after slash split: {slash_parts}")
            
            if len(slash_parts) > 1:
                host = slash_parts[0]
                database = slash_parts[1]
                port = "5432"  # Default PostgreSQL port
                
                masked_url = f"***@{host}:{port}/{database}"
                print(f"\nTesting connection to: {masked_url}")
                print(f"Host: {host}")
                print(f"Port: {port}")
                print(f"Database: {database}")
            else:
                print("[ERROR] Invalid database URL format - missing database name")
                return False
        else:
            print("[ERROR] Invalid database URL format - missing @ symbol")
            return False
        
        # Test connection
        print("\nAttempting database connection...")
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("[SUCCESS] Database connection successful!")
            
            # Get database version
            version = connection.execute(text("SELECT version()")).scalar()
            print(f"Database version: {version}")
            
        # Test session
        print("\nTesting database session...")
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            print("[SUCCESS] Database session working!")
            
            # Create tables
            print("\nCreating database tables...")
            Base.metadata.create_all(bind=engine)
            print("[SUCCESS] Tables created successfully!")
            
            # Test data operations
            print("\nTesting data operations...")
            
            # Create a test user
            test_user = User(
                twitch_id="test123",
                username="test_user",
                channel_name="test_channel"
            )
            db.add(test_user)
            db.commit()
            print("[SUCCESS] Created test user")
            
            # Read the test user back
            saved_user = db.query(User).filter_by(username="test_user").first()
            if saved_user:
                print(f"[SUCCESS] Retrieved test user: {saved_user.username}")
                print(f"User details:")
                print(f"- Twitch ID: {saved_user.twitch_id}")
                print(f"- Channel: {saved_user.channel_name}")
                
                # Clean up test data
                db.delete(saved_user)
                db.commit()
                print("[SUCCESS] Cleaned up test data")
            else:
                print("[ERROR] Could not retrieve test user")
                
        finally:
            db.close()
        
        return True
        
    except Exception as e:
        print(f"[ERROR] Database connection failed: {str(e)}")
        print("\nDetailed error information:")
        print(traceback.format_exc())
        return False

if __name__ == "__main__":
    print("Testing database connection...")
    test_database_connection() 