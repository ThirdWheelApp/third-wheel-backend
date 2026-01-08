# Third Wheel Backend

This is the FastAPI backend for the Third Wheel couples therapy app.

## Related Repository

The frontend is at `../third-wheel` (React Native/Expo). All backend changes must remain compatible with the frontend.

## Deployment

- **Platform**: Railway (PostgreSQL + Python backend)
- **Auth**: Supabase (JWT tokens only - database is on Railway)
- **Docs**: See README.md for full deployment instructions

## Key Architecture

- FastAPI with LangGraph multi-agent system
- Supabase JWT validation for auth
- WebSocket real-time chat with streaming
- 9 PostgreSQL tables

## Important Notes

- User IDs are Supabase UUIDs (primary keys match JWT `sub` field)
- API responses use camelCase for frontend compatibility
- WebSocket URL: `/ws/chat/{sessionId}/{userId}?token=JWT`
- Contexts with secret_level > 5 are never shared in joint sessions
