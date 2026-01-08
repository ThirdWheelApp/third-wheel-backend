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
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
import json

from app.config.settings import settings
from app.db.database import engine, Base
from app.api.routes import users, sessions, checkins, websocket, groups, notifications
from app.utils.logger import get_logger
from app.utils.serializers import convert_keys_to_camel

logger = get_logger(__name__)


class CamelCaseMiddleware(BaseHTTPMiddleware):
    """
    Middleware to convert JSON response keys from snake_case to camelCase.

    This ensures frontend compatibility without changing internal Python conventions.
    Only applies to JSON responses, not WebSocket or other content types.
    """

    async def dispatch(self, request: Request, call_next):
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

                # Create new response with transformed body
                return Response(
                    content=new_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
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
