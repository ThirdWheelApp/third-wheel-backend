# Third Wheel Backend

AI couples therapy platform with FastAPI, multi-agent LLM system, WebSocket chat, and PostgreSQL.

## Architecture

- **Backend:** FastAPI + LangGraph (multi-agent system)
- **Database:** PostgreSQL (9 tables: users, groups, sessions, messages, contexts, check_ins, llm_calls, notifications)
- **Auth:** Supabase JWT validation
- **LLM:** Anthropic Claude API (or mock LLM in demo mode)
- **Real-time:** WebSocket chat with streaming support

## Features

- **Private Agent:** Individual therapy for each user
- **Joint Agent:** Couples therapy with LangGraph orchestration
- **Privacy Levels:** Secret levels 1-10 control context sharing
- **Inter-Agent Communication:** Private agents can query each other
- **Notifications:** Real-time WebSocket notifications
- **Streaming:** Token-by-token response streaming
- **Check-ins:** Accountability tasks with verification workflow
- **Demo Mode:** Full functionality without API costs

---

## Deployment Modes

### 1. Demo Mode (No API Costs)

Perfect for testing and demos without incurring Anthropic API costs.

```bash
# Set in .env
DEMO_MODE=true
ANTHROPIC_API_KEY=  # Can be empty in demo mode

# Create demo data
python scripts/seed_demo_data.py

# Start server
./start.sh
```

Demo mode uses mock LLM responses that simulate realistic therapy conversations.

### 2. Local Development

For developing with real LLM responses.

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your real credentials

# 2. Install and start
./start.sh

# 3. Optionally seed demo data
python scripts/seed_demo_data.py
```

**Prerequisites:**
- Python 3.11+
- PostgreSQL running locally
- Supabase account (for auth)
- Anthropic API key

**URLs:**
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health
- WebSocket: `ws://localhost:8000/ws/chat/{sessionId}/{userId}?token=JWT`

### 3. Production (Replit)

Full production deployment on Replit.

**Prerequisites:**
- Replit account with subscription (10GB PostgreSQL included)
- Supabase account (free tier - auth only)
- Anthropic API key

**Steps:**

1. **Enable PostgreSQL in Replit**
   - Go to Replit → Tools → PostgreSQL
   - Enable 10GB database (included with subscription)

2. **Add Secrets in Replit**
   ```
   DATABASE_URL=<auto-added by PostgreSQL tool>
   SUPABASE_URL=https://xxx.supabase.co
   SUPABASE_ANON_KEY=eyJhbGc...
   SUPABASE_JWT_SECRET=your-jwt-secret
   ANTHROPIC_API_KEY=sk-ant-api03-...
   ENVIRONMENT=production
   ALLOWED_ORIGINS=https://your-frontend.app
   ```

3. **Deploy**
   - Click "Run" button
   - Database tables auto-create on startup

4. **Verify**
   - Health: `https://your-repl-name.repl.co/health`
   - Docs: `https://your-repl-name.repl.co/docs`

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
- `GET /api/sessions/{id}` - Get session
- `PUT /api/sessions/{id}/status` - Update status

### Check-ins
- `GET /api/checkins` - Get user's check-ins
- `POST /api/checkins` - Create check-in
- `PUT /api/checkins/{id}/complete` - Mark complete
- `PUT /api/checkins/{id}/verify` - Verify completion

### Notifications
- `GET /api/notifications` - Get user's notifications
- `GET /api/notifications/count` - Get unread count
- `PUT /api/notifications/{id}/read` - Mark as read
- `PUT /api/notifications/read-all` - Mark all as read
- `DELETE /api/notifications/{id}` - Delete notification

### WebSocket
- `WS /ws/chat/{sessionId}/{userId}?token=JWT` - Real-time chat

Full API docs available at `/docs` (Swagger UI)

---

## WebSocket Protocol

### Connection
```
ws://host/ws/chat/{sessionId}/{userId}?token=JWT
```

### Incoming Messages
```json
{"type": "message", "content": "Hello", "stream": true}
{"type": "typing_start"}
{"type": "typing_stop"}
```

### Outgoing Messages
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

9 tables total:
- `users` - User accounts (Supabase UUID as primary key)
- `groups` - Relationship pairs (partner1_id, partner2_id)
- `sessions` - Therapy sessions (private/joint, with invite_message)
- `messages` - Chat messages (with privacy_level)
- `private_user_context` - User-specific context (with secret levels 1-10)
- `group_context` - Shared relationship context
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

See `.env.example` for full list.

---

## Development Scripts

```bash
# Seed demo data
python scripts/seed_demo_data.py

# Start with auto-reload
./start.sh

# Manual start
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Important Notes

- **Field Names:** API responses use camelCase (frontend-compatible)
- **User IDs:** Supabase UUID used as primary key
- **WebSocket URL:** Includes both sessionId and userId in path
- **Streaming:** Available for private sessions via `stream: true`
- **Privacy:** Contexts with secret_level > 5 never shared in joint sessions
