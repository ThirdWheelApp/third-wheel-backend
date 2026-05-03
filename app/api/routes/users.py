"""
User API Routes
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import httpx
from app.db.database import get_db
from app.db.models import User
from app.schemas.schemas import UserCreate, UserResponse, InvitePartnerRequest, InvitePartnerResponse
from app.utils.auth import get_current_user, get_current_user_optional
from app.utils.supabase_admin import invite_user_by_email, is_admin_configured
from app.utils.logger import get_logger
from app.config.settings import settings
from app.services.invitation_service import create_or_reuse_pending_relationship, normalize_email
import uuid
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = get_logger(__name__)

router = APIRouter()


def _is_allowed_invite_redirect(url: Optional[str]) -> bool:
    """Allow http(s) web URLs and the native thirdwheel deep link scheme."""
    if not url:
        return False
    normalized = url.strip().lower()
    return normalized.startswith("http://") or normalized.startswith("https://") or normalized.startswith("thirdwheel://")


def _build_canonical_invite_redirect(
    base_redirect: str,
    invited_email: str,
    inviter_email: Optional[str],
    inviter_name: str,
    inviter_id: str,
    group_id: Optional[str] = None,
    relationship_type: Optional[str] = None,
    is_long_distance: Optional[bool] = None,
) -> str:
    """
    Normalize invite redirects so invitees always land in onboarding with prefilled context.

    This avoids ambiguous root redirects (e.g. http://localhost:8081) that can drop users on login.
    """
    invite_params = {
        "mode": "invite",
        "email": invited_email.lower(),
        "inviterName": inviter_name,
        "invitedBy": inviter_id,
    }
    if group_id:
        invite_params["groupId"] = group_id
    if inviter_email:
        invite_params["partnerEmail"] = inviter_email.lower()
    if relationship_type:
        invite_params["relationshipType"] = relationship_type
    if is_long_distance is not None:
        invite_params["isLongDistance"] = "true" if is_long_distance else "false"

    parsed = urlparse(base_redirect)

    if parsed.scheme == "thirdwheel":
        return f"thirdwheel://onboarding?{urlencode(invite_params)}"

    existing_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing_query.update(invite_params)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        "/onboarding",
        "",
        urlencode(existing_query),
        "",
    ))


@router.post("/initialize", response_model=UserResponse)
async def initialize_user(
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """
    Initialize user after Supabase signup.

    Called by frontend after supabase.auth.signUp().
    Creates user record in our database using Supabase user ID as primary key.

    IMPORTANT: This endpoint is intentionally NOT authenticated since it's
    called immediately after signup before the user is initialized in our DB.
    """
    if not user_data.supabase_user_id:
        raise HTTPException(status_code=400, detail="supabase_user_id is required")

    # Check if user already exists (by Supabase ID)
    existing = db.query(User).filter(
        User.id == uuid.UUID(user_data.supabase_user_id)
    ).first()

    if existing:
        return existing

    # Also check by email (handles re-signup with new Supabase ID)
    existing_by_email = db.query(User).filter(
        User.email == user_data.email
    ).first()

    if existing_by_email:
        # User re-signed up with new Supabase account - migrate their ID
        new_id = uuid.UUID(user_data.supabase_user_id)
        if existing_by_email.id != new_id:
            old_id = existing_by_email.id
            logger.warning(
                "User email already exists with different ID; migrating user ID "
                f"old_id={old_id} new_id={new_id}"
            )

            # Update array references that are not covered by FK cascades.
            db.execute(
                text(
                    """
                    UPDATE sessions
                    SET participants = array_replace(participants, :old_id, :new_id)
                    WHERE :old_id = ANY(participants)
                    """
                ),
                {"old_id": old_id, "new_id": new_id},
            )

            existing_by_email.id = new_id

        existing_by_email.name = user_data.name  # Update name too in case it changed
        db.commit()
        db.refresh(existing_by_email)
        return existing_by_email

    # Create new user with Supabase UUID as primary key
    user = User(
        id=uuid.UUID(user_data.supabase_user_id),  # Use Supabase ID as primary key
        email=user_data.email,
        name=user_data.name
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get current authenticated user's profile.

    Extracts user ID from JWT token and returns their profile.
    """
    user = db.query(User).filter(User.id == uuid.UUID(current_user_id)).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found. Please initialize your account."
        )

    return user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user by ID (protected endpoint).

    Requires authentication. Users can view other users' profiles
    only if they're in a group together (future enhancement).
    """
    user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # TODO: Add authorization - only allow if users are in same group
    # For now, allow any authenticated user to view profiles

    return user


@router.post("/invite-partner", response_model=InvitePartnerResponse)
async def invite_partner(
    request: Request,
    invite_data: InvitePartnerRequest,
    current_user_id: Optional[str] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """
    Send partner invitation email via Supabase.

    Uses Supabase's inviteUserByEmail API to send an invitation email
    to the partner. The partner will receive an email with a link to
    sign up for the app.

    Authentication is optional:
    - If authenticated: Uses current_user_id from token
    - If not authenticated: Uses inviter_user_id from request body (for onboarding flow)

    Requires SUPABASE_SERVICE_ROLE_KEY when INVITE_EMAIL_DELIVERY_ENABLED=true.
    """
    logger.info(f"Partner invitation request: partner_email={invite_data.partner_email}, "
                f"authenticated_user={current_user_id}, "
                f"inviter_user_id_from_body={invite_data.inviter_user_id}")

    # Determine the inviter user ID
    # Priority: authenticated user > body parameter
    inviter_id = current_user_id or invite_data.inviter_user_id

    if not inviter_id:
        logger.warning("Partner invitation failed: No user ID provided (neither auth nor body)")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID is required. Provide authentication or inviterUserId in body."
        )

    # Get inviter user details from our DB; this is required so the backend can
    # own pending relationship state before any email is delivered.
    inviter_email = invite_data.inviter_email
    try:
        inviter = db.query(User).filter(User.id == uuid.UUID(inviter_id)).first()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid inviter user ID")

    if not inviter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inviter user not found")

    inviter_name = inviter.name or invite_data.inviter_name
    inviter_email = inviter.email if inviter else inviter_email

    if normalize_email(str(invite_data.partner_email)) == normalize_email(inviter.email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot invite yourself")

    group = create_or_reuse_pending_relationship(
        db,
        inviter=inviter,
        invited_email=str(invite_data.partner_email),
        relationship_type=invite_data.relationship_type,
        relationship_description=invite_data.relationship_description,
        is_long_distance=invite_data.is_long_distance,
    )
    db.commit()
    db.refresh(group)

    try:
        # Redirect priority:
        # 1) Client-provided redirectTo (e.g., web origin from frontend)
        # 2) Explicit configured URL (recommended for production)
        # 3) Request origin (useful for local web testing)
        # 4) Native deep link fallback
        request_origin = request.headers.get("origin")
        redirect_to = (
            invite_data.redirect_to
            if _is_allowed_invite_redirect(invite_data.redirect_to)
            else None
        )
        if not redirect_to and _is_allowed_invite_redirect(settings.INVITE_REDIRECT_URL):
            redirect_to = settings.INVITE_REDIRECT_URL
        if not redirect_to and _is_allowed_invite_redirect(request_origin):
            redirect_to = request_origin
        if not _is_allowed_invite_redirect(redirect_to):
            redirect_to = "thirdwheel://signup"
        redirect_to = _build_canonical_invite_redirect(
            base_redirect=redirect_to,
            invited_email=str(invite_data.partner_email),
            inviter_email=inviter_email,
            inviter_name=inviter_name,
            inviter_id=inviter_id,
            group_id=str(group.id),
            relationship_type=group.relationship_type,
            is_long_distance=group.is_long_distance,
        )

        if settings.INVITE_EMAIL_DELIVERY_ENABLED:
            if not is_admin_configured():
                logger.error("Partner invitation failed: SUPABASE_SERVICE_ROLE_KEY not configured")
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail="Partner invitation feature is not configured. Please set SUPABASE_SERVICE_ROLE_KEY."
                )

            # Send invitation using direct HTTP call to Supabase Admin API.
            # This avoids dependency issues with the supabase-py client.
            await invite_user_by_email(
                email=invite_data.partner_email,
                redirect_to=redirect_to,
                data={
                    "invited_by": inviter_id,
                    "inviter_name": inviter_name,
                    "inviter_email": inviter_email,
                    "group_id": str(group.id),
                    "relationship_type": group.relationship_type,
                    "relationship_description": group.relationship_description,
                    "is_long_distance": group.is_long_distance,
                    "needs_onboarding": True,
                }
            )
        else:
            logger.info("Invite email delivery disabled; returning invite URL without sending email")

        logger.info(f"Partner invitation sent successfully to {invite_data.partner_email} by user {inviter_id}")

        return InvitePartnerResponse(
            success=True,
            message=f"Invitation prepared for {invite_data.partner_email}",
            group_id=group.id,
            invite_url=redirect_to,
        )

    except httpx.HTTPStatusError as e:
        # Surface actionable errors from Supabase instead of collapsing into HTTP 500.
        upstream_status = e.response.status_code if e.response else status.HTTP_502_BAD_GATEWAY
        upstream_body = e.response.text if e.response else str(e)
        normalized_body = upstream_body.lower()

        if upstream_status == status.HTTP_429_TOO_MANY_REQUESTS:
            detail = "Invite rate limit reached. Please wait a few minutes and try again."
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)

        if upstream_status == status.HTTP_422_UNPROCESSABLE_ENTITY:
            if "already" in normalized_body and ("registered" in normalized_body or "invited" in normalized_body):
                detail = "This partner has already been invited or already has an account."
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

            detail = (
                "Invite configuration is invalid. Check Supabase Auth URL configuration "
                "(Site URL and redirect allowlist)."
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Invite provider error ({upstream_status}). Please try again."
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Failed to send partner invitation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send invitation: {str(e)}"
        )
