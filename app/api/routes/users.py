"""
User API Routes
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import User
from app.schemas.schemas import UserCreate, UserResponse, InvitePartnerRequest, InvitePartnerResponse
from app.utils.auth import get_current_user
from app.utils.supabase_admin import get_supabase_admin, is_admin_configured
from app.utils.logger import get_logger
import uuid

logger = get_logger(__name__)

router = APIRouter()


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
        # User re-signed up with new Supabase account - update their ID
        existing_by_email.id = uuid.UUID(user_data.supabase_user_id)
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
    invite_data: InvitePartnerRequest,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Send partner invitation email via Supabase.

    Uses Supabase's inviteUserByEmail API to send an invitation email
    to the partner. The partner will receive an email with a link to
    sign up for the app.

    Requires authentication and SUPABASE_SERVICE_ROLE_KEY to be configured.
    """
    # Check if admin client is configured
    if not is_admin_configured():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Partner invitation feature is not configured. Please set SUPABASE_SERVICE_ROLE_KEY."
        )

    # Get inviter user for name
    inviter = db.query(User).filter(User.id == uuid.UUID(current_user_id)).first()
    inviter_name = inviter.name if inviter else invite_data.inviter_name

    try:
        supabase = get_supabase_admin()

        # Invite the partner using Supabase admin API
        response = supabase.auth.admin.invite_user_by_email(
            invite_data.partner_email,
            options={
                "redirect_to": "thirdwheel://signup",  # Deep link to app
                "data": {
                    "invited_by": current_user_id,
                    "inviter_name": inviter_name
                }
            }
        )

        logger.info(f"Partner invitation sent to {invite_data.partner_email} by user {current_user_id}")

        return InvitePartnerResponse(
            success=True,
            message=f"Invitation sent to {invite_data.partner_email}"
        )

    except Exception as e:
        logger.error(f"Failed to send partner invitation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send invitation: {str(e)}"
        )
