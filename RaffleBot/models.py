from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, validates
import re
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Get database URL from environment, fallback to SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///giveaway.db")

# Create engine and session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    twitch_id = Column(String, unique=True, index=True, nullable=False)  
    username = Column(String, unique=True, index=True, nullable=False) 
    channel_name = Column(String, index=True, nullable=True)

    giveaways = relationship("Giveaway", back_populates="creator")
    winnings = relationship("Winner", back_populates="user")

    @validates('username')
    def validate_username(self, key, value):
        """Validate username to ensure it contains only safe characters."""
        if not re.match(r"^[a-zA-Z0-9_]+$", value):
            raise ValueError("Invalid username: must contain only letters, numbers, and underscores.")
        return value

class Giveaway(Base):
    __tablename__ = "giveaways"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    frequency = Column(Integer, nullable=False)
    threshold = Column(Integer, nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    active = Column(Boolean, default=False)


    creator = relationship("User", back_populates="giveaways")
    items = relationship(
        "Item", 
        back_populates="giveaway", 
        cascade="none"
    )
    winners = relationship("Winner", back_populates="giveaway")
    active_instances = relationship("ActiveGiveaway", back_populates="giveaway")
    process_trackers = relationship("ProcessTracker", back_populates="giveaway")

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    code = Column(String, nullable=True)
    is_won = Column(Boolean, default=False)
    giveaway_id = Column(Integer, ForeignKey("giveaways.id", ondelete="SET NULL"), nullable=True)
    winner_username = Column(String, nullable=True)

    giveaway = relationship("Giveaway", back_populates="items")

class Winner(Base):
    __tablename__ = "winners"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    giveaway_id = Column(Integer, ForeignKey("giveaways.id"))
    item_id = Column(Integer, ForeignKey("items.id"))

    user = relationship("User", back_populates="winnings")
    giveaway = relationship("Giveaway", back_populates="winners")
    item = relationship("Item")

class ActiveGiveaway(Base):
    __tablename__ = "active_giveaways"
    id = Column(Integer, primary_key=True, index=True)
    giveaway_id = Column(Integer, ForeignKey("giveaways.id"), unique=True, nullable=False)
    process_id = Column(Integer, nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    channel_name = Column(String, nullable=False)
    should_stop = Column(Boolean, default=False, nullable=False)
    
    giveaway = relationship("Giveaway", back_populates="active_instances")

class ProcessTracker(Base):
    __tablename__ = "process_trackers"
    id = Column(Integer, primary_key=True, index=True)
    process_id = Column(Integer, nullable=False)
    giveaway_id = Column(Integer, ForeignKey("giveaways.id"), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_heartbeat = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    
    giveaway = relationship("Giveaway", back_populates="process_trackers")

Base.metadata.create_all(bind=engine)
