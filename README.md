# Discord‑Clone

A minimal Discord‑like chat application with real‑time messaging, authentication,
and several unique features.

## Stack

* **Backend** – FastAPI (Python) + WebSocket, async SQLAlchemy + asyncpg  
* **Database** – PostgreSQL  
* **Frontend** – plain HTML / CSS / JavaScript (no framework)  
* **Hosting** – Render.com (Dockerfile provided)

## Prerequisites

* Python 3.9+  
* PostgreSQL instance  
* `uvicorn` for running the API  
* Node is **not** required – the frontend is static files.

Create a file `.env` in the project root:

```dotenv
DATABASE_URL=postgresql+asyncpg://user:password@localhost/discord_clone
SECRET_KEY=super‑secret‑key‑change‑me
OPENROUTER_API_KEY=your_openrouter_key   # for AI Assistant
GITHUB_TOKEN=your_github_token           # for GitHub integration
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run database migrations (creates tables):

```bash
python -c "import asyncio; from database import init_db; asyncio.run(init_db())"
```

Start the server:

```bash
uvicorn app:app --reload
```

Open `frontend/index.html` in a browser (or serve the `frontend` folder with any static server).  
The client will communicate with the backend on `http://localhost:8000`.

---

## Unique Features

| # | Feature | Description | Implementation |
|---|---------|-------------|----------------|
| 1 | **AI Assistant** | Users can mention `@assistant` in any channel. The assistant replies using the OpenRouter API (GPT‑4‑turbo). | `app.py` → WebSocket handler checks messages for `@assistant` and calls `fetch_ai_reply`. |
| 2 | **Server Analytics** | Dashboard shows daily active users, message volume, and top contributors per server. | `/analytics/{server_id}` endpoint returns aggregated data; `analytics.html` renders charts with Chart.js. |
| 3 | **Auto‑Moderation** | Simple profanity filter (English/Russian) automatically deletes or masks messages containing banned words. | `moderation.py` runs before storing a message; a configurable word list lives in that file. |
| 4 | **Level & Achievement System** | Users earn XP for each sent message; level‑up notifications appear in the channel. | Background task updates `users.xp`; `MessageCreate` endpoint awards XP and checks for level up. |
| 5 | **GitHub Integration** | A “GitHub” channel can be linked to a repository; recent commits appear automatically. | `/github/{owner}/{repo}/commits` endpoint fetches the latest commits via GitHub REST API; displayed in a dedicated channel. |

---

## Project Layout

```
discord-clone/
├─ app.py                # FastAPI + WS server (all backend logic)
├─ database.py           # Async SQLAlchemy models & DB helpers
├─ moderation.py         # Profanity filter utilities
├─ analytics.py          # Server analytics helpers
├─ requirements.txt
├─ .env
└─ frontend/
   ├─ index.html
   ├─ login.html
   ├─ register.html
   ├─ server.html
   └─ channel.html
```

---

## Running with Docker (Render.com)

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Push the repo to Render, set the environment variables (`DATABASE_URL`, `SECRET_KEY`, `OPENROUTER_API_KEY`, `GITHUB_TOKEN`) in the dashboard, and Deploy.

---

## Development Tips

* Hot‑reload is enabled with `--reload`.  
* All API routes (except `/register` and `/login`) require a valid JWT.  
* WebSocket connections must include `?token=JWT` query param.  
* Frontend scripts store the JWT in `localStorage` and attach it to every request.  

Happy coding! 🚀