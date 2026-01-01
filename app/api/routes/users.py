"""
User API Routes
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import User
from app.schemas.schemas import UserCreate, UserResponse
from app.utils.auth import get_current_user
import uuid

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
