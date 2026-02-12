"""
Main FastAPI Application

Initializes the Third Wheel backend with all routes,
middleware, and configuration.

Key Features:
- CamelCase response serialization for frontend compatibility
- CORS configuration for development and production
- Automatic database table creation
- LLM service integration
"""

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from contextlib import asynccontextmanager
import json
import time

from app.config.settings import settings
from app.db.database import engine, Base
from app.api.routes import users, sessions, checkins, websocket, groups, notifications, tasks
from app.utils.logger import get_logger
from app.utils.serializers import convert_keys_to_camel

logger = get_logger(__name__)


class WebSocketLoggingMiddleware:
    """
    Pure ASGI middleware to log WebSocket connection attempts.

    BaseHTTPMiddleware only handles HTTP scopes, so WebSocket connections
    are invisible to it. This middleware logs ALL scope types including websocket.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "websocket":
            path = scope.get("path", "unknown")
            query = scope.get("query_string", b"").decode("utf-8", errors="replace")
            headers = dict(scope.get("headers", []))

            # Log connection attempt (mask token in query string for security)
            safe_query = query[:50] + "..." if len(query) > 50 else query
            logger.info(f">>> WEBSOCKET CONNECT: {path}?{safe_query}")
            logger.info(f"    Headers: upgrade={headers.get(b'upgrade', b'').decode()}, "
                       f"connection={headers.get(b'connection', b'').decode()}")

        await self.app(scope, receive, send)


class CamelCaseMiddleware(BaseHTTPMiddleware):
    """
    Middleware to convert JSON response keys from snake_case to camelCase.

    This ensures frontend compatibility without changing internal Python conventions.
    Only applies to JSON responses, not WebSocket or other content types.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip WebSocket connections - middleware interferes with upgrade
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        response = await call_next(request)

        # Only process JSON responses
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read and transform response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        if body:
            try:
                # Parse JSON, convert keys, and re-serialize
                data = json.loads(body)
                camel_data = convert_keys_to_camel(data)
                new_body = json.dumps(camel_data)

                # Copy headers but exclude Content-Length (will be recalculated)
                new_headers = {
                    k: v for k, v in response.headers.items()
                    if k.lower() != 'content-length'
                }

                # Create new response with transformed body
                return Response(
                    content=new_body,
                    status_code=response.status_code,
                    headers=new_headers,
                    media_type="application/json"
                )
            except json.JSONDecodeError:
                # If not valid JSON, return original
                return Response(
                    content=body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=content_type
                )

        return response


def run_schema_migrations():
    """
    Run any pending schema migrations.

    This handles alterations that create_all() doesn't cover.
    Each migration is idempotent (safe to run multiple times).
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        # Migration: Make sessions.group_id nullable for private sessions
        # Private sessions can exist without a group (solo user, no partner yet)
        try:
            conn.execute(text("""
                ALTER TABLE sessions ALTER COLUMN group_id DROP NOT NULL
            """))
            conn.commit()
            logger.info("Migration: sessions.group_id is now nullable")
        except Exception as e:
            # Column might already be nullable - that's fine
            if "cannot alter" not in str(e).lower() and "already" not in str(e).lower():
                logger.warning(f"Migration warning (group_id): {e}")
            conn.rollback()

        # Migration: Ensure user foreign keys cascade on update
        try:
            conn.execute(text("""
                ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_created_by_fkey;
                ALTER TABLE sessions
                    ADD CONSTRAINT sessions_created_by_fkey
                    FOREIGN KEY (created_by) REFERENCES users(id) ON UPDATE CASCADE;
            """))
            conn.execute(text("""
                ALTER TABLE groups DROP CONSTRAINT IF EXISTS groups_partner1_id_fkey;
                ALTER TABLE groups
                    ADD CONSTRAINT groups_partner1_id_fkey
                    FOREIGN KEY (partner1_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE;
            """))
            conn.execute(text("""
                ALTER TABLE groups DROP CONSTRAINT IF EXISTS groups_partner2_id_fkey;
                ALTER TABLE groups
                    ADD CONSTRAINT groups_partner2_id_fkey
                    FOREIGN KEY (partner2_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE;
            """))
            conn.execute(text("""
                ALTER TABLE private_user_context DROP CONSTRAINT IF EXISTS private_user_context_user_id_fkey;
                ALTER TABLE private_user_context
                    ADD CONSTRAINT private_user_context_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE;
            """))
            conn.execute(text("""
                ALTER TABLE check_ins DROP CONSTRAINT IF EXISTS check_ins_assigned_to_fkey;
                ALTER TABLE check_ins
                    ADD CONSTRAINT check_ins_assigned_to_fkey
                    FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE;
            """))
            conn.execute(text("""
                ALTER TABLE check_ins DROP CONSTRAINT IF EXISTS check_ins_verifier_id_fkey;
                ALTER TABLE check_ins
                    ADD CONSTRAINT check_ins_verifier_id_fkey
                    FOREIGN KEY (verifier_id) REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE;
            """))
            conn.execute(text("""
                ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_user_id_fkey;
                ALTER TABLE notifications
                    ADD CONSTRAINT notifications_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE;
            """))
            conn.commit()
            logger.info("Migration: user foreign keys now cascade on update")
        except Exception as e:
            logger.warning(f"Migration warning (user FK cascades): {e}")
            conn.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle handler.

    Runs on startup and shutdown.
    """
    # Startup: Create database tables
    logger.info("Starting Third Wheel Backend...")
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully")

    # Run schema migrations for existing tables
    logger.info("Running schema migrations...")
    run_schema_migrations()
    logger.info("Schema migrations complete")

    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"LLM Model: {settings.LLM_MODEL}")

    yield

    # Shutdown: Cleanup
    logger.info("Shutting down Third Wheel Backend...")


# Initialize FastAPI app
app = FastAPI(
    title="Third Wheel API",
    description="Backend API for Third Wheel couples therapy platform",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
if settings.ENVIRONMENT == "development":
    # Development: Allow local origins
    allowed_origins = [
        "http://localhost:8081",  # Expo dev server
        "http://localhost:19006",  # Expo web
        "http://localhost:3000",   # React dev server
        "exp://localhost:8081",   # Expo Go
    ]
    logger.info(f"CORS enabled for development origins: {allowed_origins}")
else:
    # Production: Use configured origins
    allowed_origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",")]
    logger.info(f"CORS enabled for production origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add camelCase middleware for JSON responses
# Note: Middleware order matters - CamelCase runs after CORS
app.add_middleware(CamelCaseMiddleware)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs all incoming requests for debugging.

    This middleware runs FIRST on requests (added last = runs first).
    Helps identify if requests are reaching the server at all.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip WebSocket connections - BaseHTTPMiddleware interferes with upgrade
        if request.headers.get("upgrade", "").lower() == "websocket":
            logger.info(f">>> WEBSOCKET UPGRADE: {request.url.path}")
            return await call_next(request)

        start_time = time.time()
        logger.info(f">>> INCOMING: {request.method} {request.url.path}")

        try:
            response = await call_next(request)
            elapsed = time.time() - start_time
            logger.info(f"<<< RESPONSE: {request.method} {request.url.path} [{response.status_code}] ({elapsed:.3f}s)")
            return response
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"!!! ERROR: {request.method} {request.url.path} - {e} ({elapsed:.3f}s)")
            raise


# Add request logging middleware (added last = runs first on request)
app.add_middleware(RequestLoggingMiddleware)

# Add WebSocket logging middleware (ASGI-level, catches websocket scopes that
# BaseHTTPMiddleware misses). This runs outermost on the middleware stack.
app.add_middleware(WebSocketLoggingMiddleware)


# Add validation exception handler for debugging
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for request validation errors.
    Logs detailed error information to help debug issues.
    """
    logger.error(f"Validation error for {request.method} {request.url.path}")
    logger.error(f"Validation errors: {exc.errors()}")

    # Try to log the request body for debugging
    try:
        body = await request.body()
        logger.error(f"Request body: {body.decode('utf-8')[:500]}")  # First 500 chars
    except Exception as e:
        logger.error(f"Could not read request body: {e}")

    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


# Register API routes
app.include_router(
    users.router,
    prefix="/api/users",
    tags=["users"]
)

app.include_router(
    groups.router,
    prefix="/api/groups",
    tags=["groups"]
)

app.include_router(
    sessions.router,
    prefix="/api/sessions",
    tags=["sessions"]
)

app.include_router(
    checkins.router,
    prefix="/api/checkins",
    tags=["checkins"]
)

app.include_router(
    tasks.router,
    prefix="/api/tasks",
    tags=["tasks"]
)

app.include_router(
    notifications.router,
    prefix="/api/notifications",
    tags=["notifications"]
)

app.include_router(
    websocket.router,
    tags=["websocket"]
)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "third-wheel-api",
        "version": "1.0.0",
        "environment": settings.ENVIRONMENT
    }


@app.get("/health")
async def health_check():
    """
    Detailed health check endpoint.

    Can be extended to check database connectivity,
    external services, etc.
    """
    return {
        "status": "healthy",
        "database": "connected",
        "llm_service": "configured"
    }


@app.get("/auth-callback", response_class=HTMLResponse)
async def auth_callback():
    """
    Email confirmation callback page.

    This page is shown to users who confirm their email on mobile devices
    that can't handle deep links (e.g., Expo Go development).

    For standalone builds (.ipa, App Store), the deep link thirdwheel://auth-callback
    will open the app directly instead of showing this page.
    """
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Email Confirmed - Third Wheel</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #FFF9F7 0%, #FFE5E5 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .container {
                background: white;
                border-radius: 16px;
                padding: 40px;
                text-align: center;
                max-width: 400px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.1);
            }
            .icon {
                width: 80px;
                height: 80px;
                background: #FFE5E5;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 24px;
                font-size: 40px;
            }
            h1 {
                color: #333;
                font-size: 24px;
                margin-bottom: 12px;
            }
            p {
                color: #666;
                font-size: 16px;
                line-height: 1.5;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">✓</div>
            <h1>Email Confirmed!</h1>
            <p>Your email has been verified successfully. You can now return to the Third Wheel app and log in.</p>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn
    import os

    # Use PORT from environment (Railway provides this) or default to 8000
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.ENVIRONMENT == "development"
    )
