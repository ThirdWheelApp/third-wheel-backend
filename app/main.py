"""
Main FastAPI Application

Initializes the Third Wheel backend with all routes,
middleware, and configuration.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config.settings import settings
from app.db.database import engine, Base
from app.api.routes import users, sessions, checkins, websocket, groups
from app.utils.logger import get_logger

logger = get_logger(__name__)


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

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.ENVIRONMENT == "development"
    )
