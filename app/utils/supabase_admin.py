"""
Supabase Admin Utilities

Provides admin-level access to Supabase using the service role key.
Used for operations that require elevated privileges like inviteUserByEmail.

Uses direct HTTP calls to avoid dependency issues with the supabase-py client.

Security Note:
    The service role key has full database access and must NEVER be exposed
    on the client side. Only use this for server-side operations.
"""

import httpx
from app.config.settings import settings
from app.utils.logger import get_logger
from typing import Optional, Dict, Any

logger = get_logger(__name__)


def is_admin_configured() -> bool:
    """
    Check if Supabase admin features can be used.

    Returns:
        True if SUPABASE_SERVICE_ROLE_KEY is set
    """
    return bool(settings.SUPABASE_SERVICE_ROLE_KEY)


async def invite_user_by_email(
    email: str,
    redirect_to: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Invite a user by email using Supabase Admin API.

    Makes a direct HTTP call to Supabase's admin invite endpoint,
    avoiding the need for the full supabase-py client which has
    dependency conflicts with websockets.

    Args:
        email: Email address to invite
        redirect_to: Optional redirect URL after signup
        data: Optional metadata to include with the invitation

    Returns:
        Response from Supabase API

    Raises:
        ValueError: If Supabase credentials are not configured
        httpx.HTTPStatusError: If the API call fails
    """
    if not settings.SUPABASE_URL:
        raise ValueError("SUPABASE_URL not configured")

    if not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise ValueError(
            "SUPABASE_SERVICE_ROLE_KEY not configured. "
            "This is required for partner invitation feature."
        )

    # Supabase Admin API endpoint for inviting users
    url = f"{settings.SUPABASE_URL}/auth/v1/invite"

    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "email": email,
    }

    if redirect_to:
        # Supabase expects redirect_to at top-level payload key.
        payload["redirect_to"] = redirect_to

    if data:
        payload["data"] = {**payload.get("data", {}), **data}

    logger.info(f"Sending Supabase invite to {email}")

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=30.0)

        if response.status_code >= 400:
            error_detail = response.text
            logger.error(f"Supabase invite failed: {response.status_code} - {error_detail}")
            response.raise_for_status()

        result = response.json()
        logger.info(f"Supabase invite successful for {email}")
        return result
