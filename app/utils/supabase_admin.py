"""
Supabase Admin Client

Provides admin-level access to Supabase using the service role key.
Used for operations that require elevated privileges like inviteUserByEmail.

Security Note:
    The service role key has full database access and must NEVER be exposed
    on the client side. Only use this for server-side operations.
"""

from supabase import create_client, Client
from app.config.settings import settings
from app.utils.logger import get_logger
from typing import Optional

logger = get_logger(__name__)

_supabase_admin: Optional[Client] = None


def get_supabase_admin() -> Client:
    """
    Get Supabase admin client with service role key.

    Returns a singleton client instance for admin operations.

    Returns:
        Supabase client configured with service role key

    Raises:
        ValueError: If Supabase credentials are not configured
    """
    global _supabase_admin

    if _supabase_admin is None:
        if not settings.SUPABASE_URL:
            raise ValueError("SUPABASE_URL not configured")

        if not settings.SUPABASE_SERVICE_ROLE_KEY:
            raise ValueError(
                "SUPABASE_SERVICE_ROLE_KEY not configured. "
                "This is required for partner invitation feature."
            )

        logger.info("Initializing Supabase admin client")
        _supabase_admin = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY
        )

    return _supabase_admin


def is_admin_configured() -> bool:
    """
    Check if Supabase admin client can be configured.

    Returns:
        True if SUPABASE_SERVICE_ROLE_KEY is set
    """
    return bool(settings.SUPABASE_SERVICE_ROLE_KEY)
