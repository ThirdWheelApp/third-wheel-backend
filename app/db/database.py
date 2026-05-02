"""
Database Configuration and Session Management

This module sets up SQLAlchemy for database operations.
It provides:
- Database engine configuration
- Session management
- Dependency injection for FastAPI
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import Any, Generator
from app.config.settings import settings


def _engine_connect_args() -> dict[str, Any]:
    """Return driver-specific connection options."""
    if settings.DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
        return {"connect_timeout": settings.DB_CONNECT_TIMEOUT_SECONDS}
    return {}


# Create SQLAlchemy engine
# echo=True in development for SQL logging
engine = create_engine(
    settings.DATABASE_URL,
    echo=(settings.ENVIRONMENT == "development"),
    pool_pre_ping=True,  # Verify connections before using
    pool_size=5,         # Connection pool size
    max_overflow=10,     # Max connections beyond pool_size
    connect_args=_engine_connect_args()
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class for all ORM models
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency injection for database sessions in FastAPI.

    Usage in FastAPI endpoints:
        @app.get("/users/{user_id}")
        async def get_user(user_id: str, db: Session = Depends(get_db)):
            user = db.query(User).filter(User.id == user_id).first()
            return user

    Yields:
        Session: SQLAlchemy database session

    Ensures:
        - Session is properly closed after request
        - Rollback on exceptions
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Initialize database tables.

    Creates all tables defined in models.py if they don't exist.
    Should be called once during application startup.

    Note:
        In production, use Alembic migrations instead of this function.
    """
    from app.db import models  # Import models to register them
    Base.metadata.create_all(bind=engine)
