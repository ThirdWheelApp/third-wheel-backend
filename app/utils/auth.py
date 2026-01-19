"""
Authentication Utilities

Handles JWT validation from Supabase auth tokens.
Uses Supabase's JWKS endpoint for ES256 token verification.
"""

import json
import time
import httpx
from jose import jwt, JWTError, jwk
from jose.exceptions import JWKError
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config.settings import settings
from app.utils.logger import get_logger
from typing import Optional, Dict, Any

logger = get_logger(__name__)

# HTTP Bearer scheme for extracting tokens from Authorization header
security = HTTPBearer()

# Cache for JWKS to avoid fetching on every request
_jwks_cache: Dict[str, Any] = {}
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600  # Cache JWKS for 1 hour


def get_jwks() -> Dict[str, Any]:
    """
    Fetch and cache JWKS from Supabase.

    Returns cached keys if within TTL, otherwise fetches fresh keys.
    """
    global _jwks_cache, _jwks_cache_time

    # Return cached JWKS if still valid
    if _jwks_cache and (time.time() - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache

    # Fetch fresh JWKS from Supabase
    jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    logger.info(f"Fetching JWKS from: {jwks_url}")

    try:
        response = httpx.get(jwks_url, timeout=10.0)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_cache_time = time.time()
        logger.info(f"JWKS fetched successfully, {len(_jwks_cache.get('keys', []))} keys found")
        return _jwks_cache
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        # Return cached version if available, even if expired
        if _jwks_cache:
            logger.warning("Using expired JWKS cache as fallback")
            return _jwks_cache
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to fetch authentication keys"
        )


def get_signing_key(token: str) -> str:
    """
    Get the signing key for a JWT token from JWKS.

    Args:
        token: JWT token string

    Returns:
        The public key in PEM format for verification
    """
    try:
        # Get the key ID from token header
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg")

        logger.debug(f"Token header: alg={alg}, kid={kid}")

        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing key ID"
            )

        # Find matching key in JWKS
        jwks = get_jwks()
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                # Convert JWK to PEM format
                key = jwk.construct(key_data)
                return key.to_pem().decode("utf-8")

        # Key not found - try refreshing JWKS
        logger.warning(f"Key {kid} not found in cached JWKS, refreshing...")
        global _jwks_cache_time
        _jwks_cache_time = 0  # Force refresh
        jwks = get_jwks()

        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                key = jwk.construct(key_data)
                return key.to_pem().decode("utf-8")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Signing key not found for kid: {kid}"
        )

    except JWKError as e:
        logger.error(f"JWK error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signing key"
        )


def verify_token(token: str) -> dict:
    """
    Verify and decode a Supabase JWT token.

    Supabase uses ES256 algorithm with asymmetric keys.
    We fetch the public key from Supabase's JWKS endpoint.

    Args:
        token: JWT token string from client

    Returns:
        Decoded token payload containing user information

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        # Get the signing key for this token
        signing_key = get_signing_key(token)

        # Get algorithm from token header
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg", "ES256")

        # Decode and verify the token
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=[alg],
            audience="authenticated"
        )

        logger.debug(f"Token verified successfully for user: {payload.get('sub')}")
        return payload

    except JWTError as e:
        logger.error(f"JWT verification failed: {str(e)}")
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
    logger.info("get_current_user: Starting authentication")
    token = credentials.credentials
    logger.info(f"get_current_user: Token received (first 20 chars): {token[:20]}...")

    try:
        user_id = get_user_from_token(token)
        logger.info(f"get_current_user: Authentication successful for user: {user_id}")
        return user_id
    except HTTPException as e:
        logger.warning(f"get_current_user: Authentication failed: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"get_current_user: Unexpected error: {e}", exc_info=True)
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
