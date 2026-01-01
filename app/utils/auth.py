"""
Authentication Utilities

Handles JWT validation from Supabase auth tokens.
Used to protect WebSocket connections and API endpoints.
"""

from jose import jwt, JWTError
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config.settings import settings
from app.utils.logger import get_logger
from typing import Optional

logger = get_logger(__name__)

# HTTP Bearer scheme for extracting tokens from Authorization header
security = HTTPBearer()


def verify_token(token: str) -> dict:
    """
    Verify and decode a Supabase JWT token.

    Args:
        token: JWT token string from client

    Returns:
        Decoded token payload containing user information

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        # Decode JWT using Supabase JWT secret
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated"
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication credentials: {str(e)}",
        )


def get_user_from_token(token: str) -> str:
    """
    Extract user ID from JWT token.

    Args:
        token: JWT token string

    Returns:
        User ID (UUID as string)

    Raises:
        HTTPException: If token is invalid
    """
    payload = verify_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user ID"
        )
    return user_id


# FastAPI Dependencies for route protection

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    FastAPI dependency to get current authenticated user.

    Extracts and validates JWT from Authorization header.

    Usage:
        @router.get("/protected")
        async def protected_route(user_id: str = Depends(get_current_user)):
            # user_id is the authenticated user's ID
            pass

    Args:
        credentials: HTTP Authorization credentials with Bearer token

    Returns:
        User ID (UUID as string) from the validated token

    Raises:
        HTTPException: If token is missing or invalid
    """
    token = credentials.credentials
    logger.debug(f"Authenticating request with token: {token[:20]}...")

    try:
        user_id = get_user_from_token(token)
        logger.debug(f"Authentication successful for user: {user_id}")
        return user_id
    except HTTPException as e:
        logger.warning(f"Authentication failed: {e.detail}")
        raise


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
) -> Optional[str]:
    """
    FastAPI dependency for optional authentication.

    Returns user ID if token is provided and valid, None otherwise.

    Usage:
        @router.get("/optional-auth")
        async def optional_route(user_id: Optional[str] = Depends(get_current_user_optional)):
            if user_id:
                # User is authenticated
            else:
                # Anonymous access
            pass

    Args:
        credentials: HTTP Authorization credentials (optional)

    Returns:
        User ID if authenticated, None otherwise
    """
    if not credentials:
        return None

    try:
        return get_user_from_token(credentials.credentials)
    except HTTPException:
        # Invalid token treated as no auth
        return None
