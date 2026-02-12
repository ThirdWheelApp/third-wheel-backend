# Third Wheel Backend

AI couples therapy platform with FastAPI, multi-agent LLM system, WebSocket chat, and PostgreSQL.

## Architecture

- **Backend:** FastAPI + LangGraph (multi-agent system)
- **Database:** PostgreSQL (10 tables including therapist_notes)
- **Auth:** Supabase JWT validation
- **LLM:** Anthropic Claude API (or mock LLM in demo mode)
- **Real-time:** WebSocket chat with streaming support

## Features

- **Private Agent:** Individual therapy for each user
- **Joint Agent:** Couples therapy with LangGraph orchestration
- **Privacy Levels:** Secret levels 0-10 control context sharing
- **Inter-Agent Communication:** Private agents can query each other
- **Notifications:** Real-time WebSocket notifications
- **Streaming:** Token-by-token response streaming
- **Check-ins/Tasks:** Accountability tasks with acceptance and verification workflow
- **Demo Mode:** Full functionality without API costs

---

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL (local) or Railway account
- Supabase account (for auth)
- Anthropic API key (optional for demo mode)

### Local Development

```bash
# 1. Clone and configure
git clone <repo-url>
cd third-wheel-backend
cp .env.example .env
# Edit .env with your credentials

# 2. Install dependencies and start
./scripts/start.sh

# 3. Optionally seed demo data
python scripts/seed_demo_data.py
```

**URLs:**
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health
- WebSocket: `ws://localhost:8000/ws/chat/{sessionId}?token=JWT`

### Demo Mode (No API Costs)

Perfect for testing and demos without incurring Anthropic API costs.

```bash
# Set in .env
DEMO_MODE=true
ANTHROPIC_API_KEY=  # Can be empty in demo mode

# Create demo data
python scripts/seed_demo_data.py

# Start server
./scripts/start.sh
```

Demo mode uses mock LLM responses that simulate realistic therapy conversations.

---

## Production Deployment (Railway)

Railway provides managed PostgreSQL and easy deployment from GitHub.

### Step 1: Create Railway Project

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login to Railway
railway login

# Initialize project
railway init
```

### Step 2: Add PostgreSQL Database

1. In Railway dashboard, click **"New"** → **"Database"** → **"PostgreSQL"**
2. Railway automatically provisions the database and sets `DATABASE_URL`

### Step 3: Deploy Backend

**Option A: GitHub Integration (Recommended)**
1. In Railway dashboard, click **"New"** → **"GitHub Repo"**
2. Select your repository
3. Railway auto-deploys on every push

**Option B: CLI Deployment**
```bash
railway link  # Link to existing project
railway up    # Deploy current code
```

### Step 4: Configure Environment Variables

In Railway Dashboard → Your Service → **Variables**, add:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Auto | Railway provides this automatically |
| `SUPABASE_URL` | Yes | Your Supabase project URL (e.g., `https://abc.supabase.co`) |
| `SUPABASE_ANON_KEY` | Yes | Supabase public/anon key |
| `SUPABASE_JWT_SECRET` | Yes | From Supabase → Settings → API → JWT Settings |
| `ANTHROPIC_API_KEY` | If !demo | Your Anthropic API key |
| `DEMO_MODE` | No | Set `true` for mock LLM responses |
| `LLM_MODEL` | No | Default: `claude-3-haiku-20240307` |
| `ENVIRONMENT` | No | Set to `production` |
| `ALLOWED_ORIGINS` | Yes | Frontend URLs, comma-separated |
| `LOG_LEVEL` | No | Default: `INFO` |

### Step 5: Verify Deployment

```bash
# Health check
curl https://your-app.railway.app/health

# Expected response:
# {"status": "healthy", "database": "connected", "llm_service": "configured"}
```

- **API Docs:** `https://your-app.railway.app/docs`
- **WebSocket:** `wss://your-app.railway.app/ws/chat/{sessionId}?token=JWT`

---

## API Endpoints

### Users
- `POST /api/users/initialize` - Initialize after signup (no auth required)
- `GET /api/users/me` - Get current user profile
- `GET /api/users/{id}` - Get user by ID

### Groups
- `GET /api/groups/my-groups` - Get user's groups
- `GET /api/groups/user/{userId}` - Alias for my-groups
- `POST /api/groups` - Create group `{partner_id}`
- `GET /api/groups/{id}` - Get group by ID

### Sessions
- `POST /api/sessions` - Create session
- `GET /api/sessions/my` - List current user's sessions
- `GET /api/sessions/{id}` - Get session
- `GET /api/sessions/{id}/messages` - Get session transcript
- `POST /api/sessions/{id}/request-end` - Request session end
- `POST /api/sessions/{id}/end` - End and process session

### Check-ins
- `GET /api/checkins/{session_id}/proposed` - Proposed check-ins from a session
- `GET /api/checkins/{group_id}/active` - Active check-ins for current user
- `PUT /api/checkins/{id}/approve` - Approve proposed check-in
- `PUT /api/checkins/{id}/mark-done` - Mark occurrence complete
- `PUT /api/checkins/{id}/verify` - Verify completion
- `GET /api/checkins/{id}` - Get check-in by ID

### Tasks (POC)
- `GET /api/tasks/{group_id}` - List tasks for a group
- `POST /api/tasks/{task_id}/decision` - Assignee accepts/rejects proposed task
- `POST /api/tasks/{task_id}/checkins/{checkin_id}/complete` - Mark task done
- `POST /api/tasks/{task_id}/verify` - Verifier approves/rejects completion

### Notifications
- `GET /api/notifications` - Get user's notifications
- `GET /api/notifications/count` - Get unread count
- `PUT /api/notifications/{id}/read` - Mark as read
- `PUT /api/notifications/read-all` - Mark all as read
- `DELETE /api/notifications/{id}` - Delete notification

### WebSocket
- `WS /ws/chat/{sessionId}?token=JWT` - Real-time chat

Full API docs available at `/docs` (Swagger UI)

---

## WebSocket Protocol

### Connection
```
wss://your-app.railway.app/ws/chat/{sessionId}?token=JWT
```

### Client → Server Messages
```json
{"type": "message", "content": "Hello", "stream": true}
{"type": "typing_start"}
{"type": "typing_stop"}
```

### Server → Client Messages
```json
// Regular message
{"type": "message", "messageId": "...", "senderId": "...", "content": "..."}

// Streaming (when stream: true)
{"type": "streamStart", "senderId": "therapist"}
{"type": "streamToken", "token": "Hello"}
{"type": "streamToken", "token": " there"}
{"type": "streamEnd", "messageId": "...", "content": "Hello there"}

// Typing indicators
{"type": "typing", "userId": "...", "isTyping": true}

// Notifications (pushed to user)
{"type": "notification", "notification": {...}}

// Session sync on connect
{"type": "sync", "sessionStatus": "active", "messages": [...]}
```

---

## Database Schema

10 tables total:
- `users` - User accounts (Supabase UUID as primary key)
- `groups` - Relationship pairs (partner1_id, partner2_id)
- `sessions` - Therapy sessions (private/joint, with invite_message)
- `messages` - Chat messages (with privacy_level)
- `therapist_notes` - Internal therapist memory (never exposed to clients)
- `private_user_context` - User-specific context (with secret levels 0-10)
- `group_context` - Shared relationship context (written during joint sessions)
- `check_ins` - Accountability tasks (with completion_history)
- `llm_calls` - LLM usage logging
- `notifications` - User notifications

---

## Configuration

All configuration via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | - | PostgreSQL connection URL |
| `SUPABASE_URL` | Yes | - | Supabase project URL |
| `SUPABASE_ANON_KEY` | Yes | - | Supabase anon/public key |
| `SUPABASE_JWT_SECRET` | Yes | - | Supabase JWT secret |
| `DEMO_MODE` | No | false | Use mock LLM responses |
| `ANTHROPIC_API_KEY` | If !demo | - | Anthropic API key |
| `LLM_MODEL` | No | claude-3-haiku | Model to use |
| `ENVIRONMENT` | No | development | development/production |
| `SECRET_LEVEL_THRESHOLD` | No | 5 | Context sharing threshold |
| `COUPLES_MAX_SECRET_LEVEL` | No | 0 | Max private-context secrecy allowed in couples sessions |
| `PORT` | No | 8000 | Server port (Railway sets automatically) |

See `.env.example` for full list.

---

## Development Scripts

```bash
# Seed demo data
python scripts/seed_demo_data.py

# Start with production-equivalent settings
./scripts/start.sh

# Manual start
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Troubleshooting

### Railway Deployment Issues

**Database connection fails:**
- Ensure `DATABASE_URL` is set (Railway auto-provides this for PostgreSQL services)
- Check if PostgreSQL service is running in Railway dashboard

**Health check fails:**
- Check build logs in Railway dashboard
- Ensure all required env vars are set
- Verify `SUPABASE_JWT_SECRET` is correct

**WebSocket not connecting:**
- Use `wss://` (not `ws://`) for Railway production
- Ensure JWT token is valid and passed as query param
- Check CORS settings in `ALLOWED_ORIGINS`

### Local Development Issues

**Import errors:**
- Ensure virtual environment is activated
- Run `pip install -r requirements.txt`

**Database errors:**
- Ensure PostgreSQL is running
- Check `DATABASE_URL` format: `postgresql://user:pass@localhost:5432/dbname`

---

## Important Notes

- **Field Names:** API responses use camelCase (frontend-compatible)
- **User IDs:** Supabase UUID used as primary key
- **WebSocket URL:** Includes both sessionId and userId in path, token in query
- **Streaming:** Available for sessions via `stream: true`
- **Privacy:** Contexts with secret_level > 5 never shared in joint sessions
