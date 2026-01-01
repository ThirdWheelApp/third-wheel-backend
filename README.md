# Third Wheel Backend

AI couples therapy platform with FastAPI, multi-agent LLM system, WebSocket chat, and PostgreSQL.

## Architecture

- **Backend:** FastAPI + LangGraph (multi-agent system)
- **Database:** PostgreSQL (8 tables: users, groups, sessions, messages, contexts, check_ins, llm_calls)
- **Auth:** Supabase JWT validation
- **LLM:** Anthropic Claude API
- **Real-time:** WebSocket chat

## Production Deployment (Replit)

### Prerequisites
- Replit account with subscription (10GB PostgreSQL included)
- Supabase account (free tier - auth only)
- Anthropic API key

### Steps

1. **Enable PostgreSQL in Replit**
   - Go to Replit → Tools → PostgreSQL
   - Enable 10GB database (included with subscription)

2. **Add Secrets in Replit**
   - Go to Tools → Secrets
   - Add these values:
     ```
     SUPABASE_URL=https://xxx.supabase.co
     SUPABASE_ANON_KEY=eyJhbGc...
     SUPABASE_JWT_SECRET=your-jwt-secret
     ANTHROPIC_API_KEY=sk-ant-api03-...
     ENVIRONMENT=production
     LOG_LEVEL=INFO
     ```
   - Get Supabase values from: Supabase → Settings → API
   - Get Anthropic key from: console.anthropic.com

3. **Deploy**
   - Click "Run" button
   - Database tables auto-create on first startup

4. **Verify**
   - Visit: `https://your-repl-name.repl.co/health`
   - Should return: `{"status": "healthy"}`
   - API docs at: `https://your-repl-name.repl.co/docs`

5. **Get Backend URL**
   - Your backend URL: `https://your-repl-name.repl.co`
   - Use this URL in frontend `src/config/api.js`

## Local Development

### Prerequisites
- Python 3.11+
- PostgreSQL running locally
- Supabase account (for auth)
- Anthropic API key

### Steps

1. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

2. **Install and start**
   ```bash
   ./start.sh
   ```
   - Installs dependencies
   - Checks .env exists
   - Starts server on http://localhost:8000

3. **Verify**
   - API docs: http://localhost:8000/docs
   - Health check: http://localhost:8000/health
   - WebSocket: ws://localhost:8000/ws/chat/{session_id}?token=JWT

### Manual Start (if needed)
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## API Authentication

All endpoints except `/initialize` require JWT token:
```
Authorization: Bearer YOUR_SUPABASE_JWT_TOKEN
```

WebSocket auth via query parameter:
```
ws://localhost:8000/ws/chat/{session_id}?token=YOUR_JWT
```

## Database Schema

8 tables total:
- `users` - User accounts (Supabase UUID as primary key)
- `groups` - Relationship pairs (partner1_id, partner2_id)
- `sessions` - Therapy sessions (private/joint)
- `messages` - Chat messages
- `private_user_context` - User-specific context (with secret levels)
- `group_context` - Shared relationship context
- `check_ins` - Accountability tasks
- `llm_calls` - LLM usage logging

## Important Notes

- **Security:** See `AUTHENTICATION_FIXES.md` for authentication architecture details
- **User IDs:** Supabase UUID used as primary key (not auto-generated)
- **Field Names:** Groups use `partner1_id`/`partner2_id` (not member_a/member_b)
- **WebSocket URL:** Uses JWT token in query param (no user_id in path)

## API Endpoints

Key endpoints (all require auth except /initialize):
- `POST /api/users/initialize` - Initialize user after Supabase signup (no auth)
- `GET /api/users/me` - Get current user profile
- `GET /api/groups/my-groups` - Get current user's groups
- `POST /api/groups` - Create group `{partner_id}`
- `POST /api/sessions` - Create session
- `WS /ws/chat/{session_id}?token=JWT` - WebSocket chat

Full API docs: `/docs` (Swagger UI)
