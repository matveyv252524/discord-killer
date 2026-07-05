import os
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Text, select, delete

# ----------------------------------------------------------------------
# Engine & Session
# ----------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    avatar_url = Column(String, nullable=True)
    status = Column(String, default="offline")
    created_at = Column(DateTime, default=datetime.utcnow)
    # XP & level for the Level & Achievement feature
    xp = Column(Integer, default=0)
    level = Column(Integer, default=1)

    # relationships
    owned_servers = relationship("Server", back_populates="owner")
    memberships = relationship("ServerMember", back_populates="user")
    messages = relationship("Message", back_populates="author")

class Server(Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    icon_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="owned_servers")
    members = relationship("ServerMember", back_populates="server")
    channels = relationship("Channel", back_populates="server")

class ServerMember(Base):
    __tablename__ = "server_members"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    role = Column(String, default="member")  # member, admin, moderator
    joined_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="memberships")
    server = relationship("Server", back_populates="members")

class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(Enum("text", "voice", name="channel_type"), default="text")
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    server = relationship("Server", back_populates="channels")
    messages = relationship("Message", back_populates="channel")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)

    author = relationship("User", back_populates="messages")
    channel = relationship("Channel", back_populates="messages")

# ----------------------------------------------------------------------
# Dependency helpers
# ----------------------------------------------------------------------
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

# ----------------------------------------------------------------------
# DB initialization (run once)
# ----------------------------------------------------------------------
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)