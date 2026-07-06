# -------------------------------------------------------------
# 1. Imports
# -------------------------------------------------------------
import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

import httpx

from database import (
    User,
    Server,
    ServerMember,
    Channel,
    Message,
    get_db,
    init_db,
    AsyncSessionLocal,
)
from moderation import moderate_message
from analytics import server_stats

# -------------------------------------------------------------
# 2. Configuration
# -------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API = "https://api.github.com"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI()

# CORS Middleware
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")

# -------------------------------------------------------------
# 3. Pydantic Schemas
# -------------------------------------------------------------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str

    @validator("username")
    def no_spaces(cls, v):
        if " " in v:
            raise ValueError("username cannot contain spaces")
        return v

class UserLogin(BaseModel):
    username: str
    password: str

class ServerCreate(BaseModel):
    name: str

class ChannelCreate(BaseModel):
    name: str
    type: str = "text"

class MessageCreate(BaseModel):
    content: str

# -------------------------------------------------------------
# 4. Utility functions
# -------------------------------------------------------------

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user

# -------------------------------------------------------------
# 5. Auth endpoints
# -------------------------------------------------------------
@app.post("/register", response_model=Token)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    # уникальность email и username
    existing = await db.execute(
        select(User).where((User.email == user_in.email) | (User.username == user_in.username))
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Email or username already taken")
    hashed = get_password_hash(user_in.password)
    user = User(email=user_in.email, username=user_in.username, hashed_password=hashed)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token}

@app.post("/login", response_model=Token)
async def login(form: UserLogin, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(User).where(User.username == form.username))
    user = res.scalars().first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    token = create_access_token(data={"sub": user.username})
    return {"access_token": token}

@app.get("/me")
async def me(current: User = Depends(get_current_user)):
    return {
        "id": current.id,
        "username": current.username,
        "email": current.email,
        "avatar_url": current.avatar_url,
        "status": current.status,
        "xp": current.xp,
        "level": current.level,
    }

# -------------------------------------------------------------
# 6. Server API
# -------------------------------------------------------------
async def _ensure_membership(db: AsyncSession, user_id: int, server_id: int):
    res = await db.execute(
        select(ServerMember).where(
            (ServerMember.user_id == user_id) & (ServerMember.server_id == server_id)
        )
    )
    if not res.scalars().first():
        raise HTTPException(status_code=403, detail="Not a member of this server")

@app.get("/servers", response_model=List[Dict])
async def list_servers(current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Server).join(ServerMember).where(ServerMember.user_id == current.id)
    )
    servers = res.scalars().unique().all()
    return [{"id": s.id, "name": s.name, "icon_url": s.icon_url} for s in servers]

@app.post("/servers", response_model=Dict)
async def create_server(payload: ServerCreate, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    server = Server(name=payload.name, owner_id=current.id)
    db.add(server)
    await db.commit()
    await db.refresh(server)
    member = ServerMember(user_id=current.id, server_id=server.id, role="owner")
    db.add(member)
    await db.commit()
    return {"id": server.id, "name": server.name}

@app.delete("/servers/{server_id}")
async def delete_server(server_id: int, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Server).where(Server.id == server_id))
    server = res.scalars().first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.owner_id != current.id:
        raise HTTPException(status_code=403, detail="Only owner can delete")
    await db.execute(delete(Server).where(Server.id == server_id))
    await db.commit()
    return {"detail": "deleted"}

@app.post("/servers/{server_id}/join")
async def join_server(server_id: int, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Simple join (no invite token)
    res = await db.execute(
        select(ServerMember).where((ServerMember.server_id == server_id) & (ServerMember.user_id == current.id))
    )
    if res.scalars().first():
        raise HTTPException(status_code=400, detail="Already a member")
    member = ServerMember(user_id=current.id, server_id=server_id)
    db.add(member)
    await db.commit()
    return {"detail": "joined"}

# -------------------------------------------------------------
# 7. Channel API
# -------------------------------------------------------------
@app.get("/servers/{server_id}/channels", response_model=List[Dict])
async def list_channels(server_id: int, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _ensure_membership(db, current.id, server_id)
    res = await db.execute(select(Channel).where(Channel.server_id == server_id))
    channels = res.scalars().all()
    return [{"id": c.id, "name": c.name, "type": c.type} for c in channels]

@app.post("/servers/{server_id}/channels", response_model=Dict)
async def create_channel(server_id: int, payload: ChannelCreate, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _ensure_membership(db, current.id, server_id)
    ch = Channel(name=payload.name, type=payload.type, server_id=server_id)
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return {"id": ch.id, "name": ch.name, "type": ch.type}

# -------------------------------------------------------------
# 8. Message API
# -------------------------------------------------------------
@app.get("/channels/{channel_id}/messages", response_model=List[Dict])
async def get_messages(channel_id: int, skip: int = 0, limit: int = 50, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = res.scalars().first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    await _ensure_membership(db, current.id, channel.server_id)
    stmt = (
        select(Message)
        .where(Message.channel_id == channel_id)
        .order_by(Message.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    msgs_res = await db.execute(stmt)
    msgs = msgs_res.scalars().all()
    return [
        {
            "id": m.id,
            "content": m.content,
            "author_id": m.author_id,
            "created_at": m.created_at.isoformat(),
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in reversed(msgs)
    ]

@app.post("/channels/{channel_id}/messages", response_model=Dict)
async def send_message(channel_id: int, payload: MessageCreate, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = res.scalars().first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    await _ensure_membership(db, current.id, channel.server_id)
    clean = moderate_message(payload.content)
    msg = Message(content=clean, author_id=current.id, channel_id=channel_id)
    db.add(msg)
    # XP handling
    current.xp += 5
    if current.xp >= current.level * 100:
        current.level += 1
    db.add(current)
    await db.commit()
    await db.refresh(msg)
    if "@assistant" in clean.lower():
        asyncio.create_task(_handle_ai_reply(channel_id, clean, db))
    return {"id": msg.id, "content": msg.content, "created_at": msg.created_at.isoformat()}

@app.put("/messages/{message_id}")
async def edit_message(message_id: int, payload: MessageCreate, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Message).where(Message.id == message_id))
    msg = res.scalars().first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.author_id != current.id:
        raise HTTPException(status_code=403, detail="Cannot edit others' messages")
    msg.content = moderate_message(payload.content)
    msg.updated_at = datetime.utcnow()
    await db.commit()
    return {"detail": "edited"}

@app.delete("/messages/{message_id}")
async def delete_message(message_id: int, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Message).where(Message.id == message_id))
    msg = res.scalars().first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.author_id != current.id:
        raise HTTPException(status_code=403, detail="Cannot delete others' messages")
    await db.execute(delete(Message).where(Message.id == message_id))
    await db.commit()
    return {"detail": "deleted"}

# -------------------------------------------------------------
# 9. WebSocket – real‑time chat
# -------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active: Dict[int, List[WebSocket]] = {}
    async def connect(self, ws: WebSocket, channel_id: int):
        await ws.accept()
        self.active.setdefault(channel_id, []).append(ws)
    def disconnect(self, ws: WebSocket, channel_id: int):
        self.active.get(channel_id, []).remove(ws)
    async def broadcast(self, channel_id: int, data: dict):
        for ws in self.active.get(channel_id, []):
            await ws.send_json(data)

manager = ConnectionManager()

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = None):
    if not token:
        await ws.close(code=1008)
        return
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        await ws.close(code=1008)
        return
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.username == username))
        user = res.scalars().first()
        if not user:
            await ws.close(code=1008)
            return
    init = await ws.receive_json()
    channel_id = init.get("channel_id")
    if not channel_id:
        await ws.close(code=1003)
        return
    await manager.connect(ws, channel_id)
    try:
        while True:
            data = await ws.receive_json()
            content = data.get("content", "")
            async with AsyncSessionLocal() as db:
                msg = Message(content=moderate_message(content), author_id=user.id, channel_id=channel_id)
                db.add(msg)
                # XP
                user.xp += 5
                if user.xp >= user.level * 100:
                    user.level += 1
                db.add(user)
                await db.commit()
                await db.refresh(msg)
                await manager.broadcast(channel_id, {
                    "type": "message",
                    "id": msg.id,
                    "author": user.username,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                })
                if "@assistant" in content.lower():
                    asyncio.create_task(_handle_ai_reply(channel_id, content, db))
    except WebSocketDisconnect:
        manager.disconnect(ws, channel_id)

# -------------------------------------------------------------
# 10. AI Assistant (OpenRouter)
# -------------------------------------------------------------
async def fetch_ai_reply(prompt: str) -> str:
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

async def _handle_ai_reply(channel_id: int, original_msg: str, db: AsyncSession):
    query = original_msg.replace("@assistant", "").strip()
    reply = await fetch_ai_reply(query)
    ai_msg = Message(content=reply, author_id=0, channel_id=channel_id)  # author_id=0 denotes system bot
    db.add(ai_msg)
    await db.commit()
    await db.refresh(ai_msg)
    await manager.broadcast(channel_id, {
        "type": "message",
        "id": ai_msg.id,
        "author": "assistant",
        "content": reply,
        "created_at": ai_msg.created_at.isoformat(),
    })

# -------------------------------------------------------------
# 11. Analytics endpoint
# -------------------------------------------------------------
@app.get("/analytics/{server_id}")
async def analytics(server_id: int, current: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _ensure_membership(db, current.id, server_id)
    return await server_stats(db, server_id)

# -------------------------------------------------------------
# 12. GitHub integration
# -------------------------------------------------------------
@app.get("/github/{owner}/{repo}/commits")
async def recent_commits(owner: str, repo: str, limit: int = 10):
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits?per_page={limit}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    return [
        {
            "sha": c["sha"],
            "author": c["commit"]["author"]["name"],
            "message": c["commit"]["message"],
            "date": c["commit"]["author"]["date"],
        }
        for c in data
    ]

# -------------------------------------------------------------
# 13. Startup
# -------------------------------------------------------------
@app.on_event("startup")
async def startup():
    await init_db()
