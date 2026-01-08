"""
Configuration Management for Third Wheel Backend

This module uses Pydantic Settings to manage all configuration values.
Settings are loaded from environment variables (defined in .env file).

All configurable parameters are defined here with sensible defaults.
"""

import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Configuration is divided into sections:
    - Database: PostgreSQL connection
    - Supabase: Authentication and JWT handling
    - Anthropic: LLM API configuration
    - Agent: Agent behavior parameters
    - Session: Session management settings
    - Context: Context retrieval and storage settings
    - Server: HTTP server configuration
    """

    # ==========================================
    # DATABASE CONFIGURATION
    # ==========================================
    DATABASE_URL: str

    # ==========================================
    # SUPABASE CONFIGURATION
    # ==========================================
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_JWT_SECRET: str

    # ==========================================
    # DEMO MODE CONFIGURATION
    # ==========================================
    # Enable demo mode with mock LLM responses (no API calls)
    DEMO_MODE: bool = False

    # ==========================================
    # ANTHROPIC API CONFIGURATION
    # ==========================================
    # Optional in demo mode, required otherwise
    ANTHROPIC_API_KEY: Optional[str] = None

    # ==========================================
    # LLM CONFIGURATION
    # ==========================================
    # Model to use for LLM calls (claude-3-haiku is cheapest, sonnet is smartest)
    LLM_MODEL: str = "claude-3-haiku-20240307"

    # Temperature for LLM responses (0-1, higher = more creative)
    LLM_TEMPERATURE: float = 0.7

    # Maximum tokens to generate per response
    MAX_TOKENS: int = 1024

    # ==========================================
    # AGENT CONFIGURATION
    # ==========================================
    # Maximum number of turns for multi-agent conversation
    MAX_AGENT_TURNS: int = 3

    # Maximum number of times joint agent can query each private agent per message
    MAX_PRIVATE_AGENT_QUERIES: int = 3

    # ==========================================
    # SESSION CONFIGURATION
    # ==========================================
    # Minutes of inactivity before session timeout
    SESSION_TIMEOUT_MINUTES: int = 30

    # Days before check-in approval auto-approves (0 = no auto-approval)
    CHECKIN_APPROVAL_TIMEOUT_DAYS: int = 7

    # ==========================================
    # CONTEXT CONFIGURATION
    # ==========================================
    # Maximum number of context entries to load per query (for MVP)
    # TODO: Replace with vector search when scaling
    MAX_CONTEXTS_PER_LOAD: int = 50

    # Secret level threshold for sharing in group sessions (1-10)
    # Only contexts with secret_level <= this value are shared
    SECRET_LEVEL_THRESHOLD: int = 5

    # ==========================================
    # SERVER CONFIGURATION
    # ==========================================
    HOST: str = "0.0.0.0"
    # Railway provides PORT env var dynamically
    PORT: int = int(os.getenv("PORT", 8000))
    ENVIRONMENT: str = "development"

    # Comma-separated list of allowed CORS origins for production
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8081"

    # ==========================================
    # LOGGING CONFIGURATION
    # ==========================================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # "json" or "text"

    class Config:
        """Pydantic configuration"""
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()
