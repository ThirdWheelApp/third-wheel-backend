"""
Group API Routes

Manages couples/relationship groups.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Group, User
from app.schemas.schemas import GroupAcceptInviteRequest, GroupCreate, GroupResponse
from app.services.invitation_service import accept_pending_relationship_invite, normalize_email
from app.utils.auth import get_current_user
from app.utils.logger import get_logger
from typing import List
import uuid

router = APIRouter()
logger = get_logger(__name__)


@router.post("/", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    group_data: GroupCreate,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new relationship group (couple).

    Requires authentication. Current user must be one of the two partners.

    Args:
        group_data: Group creation data with partner IDs
        current_user_id: Authenticated user ID from JWT
        db: Database session

    Returns:
        Created group information

    Raises:
        HTTPException: If users not found, validation fails, or current user not a partner
    """
    logger.info(f"User {current_user_id} creating group with partner {group_data.partner_id}")

    # Validate current user exists
    current_user = db.query(User).filter(User.id == uuid.UUID(current_user_id)).first()
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Current user not found"
        )

    # Validate partner exists
    try:
        partner = db.query(User).filter(User.id == uuid.UUID(group_data.partner_id)).first()
    except ValueError:
        logger.warning(f"Invalid UUID format for partner ID")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid partner ID format"
        )

    if not partner:
        logger.warning(f"Partner {group_data.partner_id} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Partner not found"
        )

    # Check if group already exists with these members (order independent)
    current_user_uuid = uuid.UUID(current_user_id)
    partner_uuid = uuid.UUID(group_data.partner_id)

    existing_group = db.query(Group).filter(
        ((Group.partner1_id == current_user_uuid) & (Group.partner2_id == partner_uuid)) |
        ((Group.partner1_id == partner_uuid) & (Group.partner2_id == current_user_uuid))
    ).first()

    if existing_group:
        logger.info(f"Group already exists: {existing_group.id}")
        return existing_group

    # Create new group with current user as partner1
    group = Group(
        id=uuid.uuid4(),
        partner1_id=current_user_uuid,
        partner2_id=partner_uuid,
        partner2_email=normalize_email(partner.email),
        status="active"
    )

    db.add(group)
    db.commit()
    db.refresh(group)

    logger.info(f"Group created successfully: {group.id}")
    return group


@router.get("/my-groups", response_model=List[GroupResponse])
async def get_my_groups(
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all groups that the current authenticated user is a member of.

    Requires authentication.

    Args:
        current_user_id: Authenticated user ID from JWT
        db: Database session

    Returns:
        List of groups the user belongs to
    """
    try:
        user_uuid = uuid.UUID(current_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )

    # Find all groups where user is partner1 or partner2
    groups = db.query(Group).filter(
        (Group.partner1_id == user_uuid) |
        (Group.partner2_id == user_uuid)
    ).all()

    logger.info(f"Found {len(groups)} groups for user {current_user_id}")
    return groups


@router.post("/accept-invite", response_model=GroupResponse)
async def accept_invite(
    accept_data: GroupAcceptInviteRequest,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Accept a pending relationship invite for the authenticated user.

    The authenticated user's email must match the invited email on the pending
    relationship. The client may provide invitedBy and/or groupId from the
    invite link to disambiguate multiple pending invites.
    """
    user = db.query(User).filter(User.id == uuid.UUID(current_user_id)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Current user not found")

    try:
        group = accept_pending_relationship_invite(
            db,
            invited_user=user,
            invited_by=accept_data.invited_by,
            group_id=accept_data.group_id,
        )
        db.commit()
        db.refresh(group)
        return group
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/user/{user_id}", response_model=List[GroupResponse])
async def get_groups_for_user(
    user_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all groups for a specific user.

    This is an alias for /my-groups that accepts user_id in path.
    Frontend uses this URL format for compatibility.

    Note: For security, you can only query your own groups.
    The user_id in path must match the authenticated user.

    Args:
        user_id: UUID of the user (must match authenticated user)
        current_user_id: Authenticated user ID from JWT
        db: Database session

    Returns:
        List of groups the user belongs to

    Raises:
        HTTPException: If user_id doesn't match authenticated user
    """
    # Security: Can only query your own groups
    if user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only query your own groups"
        )

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )

    # Find all groups where user is partner1 or partner2
    groups = db.query(Group).filter(
        (Group.partner1_id == user_uuid) |
        (Group.partner2_id == user_uuid)
    ).all()

    logger.info(f"Found {len(groups)} groups for user {user_id}")
    return groups


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get group by ID.

    Requires authentication. User must be a member of the group.

    Args:
        group_id: UUID of the group
        current_user_id: Authenticated user ID from JWT
        db: Database session

    Returns:
        Group information

    Raises:
        HTTPException: If group not found or user not authorized
    """
    try:
        group_uuid = uuid.UUID(group_id)
        current_user_uuid = uuid.UUID(current_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID format"
        )

    group = db.query(Group).filter(Group.id == group_uuid).first()

    if not group:
        logger.warning(f"Group {group_id} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    # Authorization: User must be a member of the group
    if group.partner1_id != current_user_uuid and group.partner2_id != current_user_uuid:
        logger.warning(f"User {current_user_id} attempted to access group {group_id} without authorization")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this group"
        )

    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a group.

    Requires authentication. User must be a member of the group.
    Warning: This will cascade delete all associated sessions, messages,
    contexts, and check-ins.

    Args:
        group_id: UUID of the group
        current_user_id: Authenticated user ID from JWT
        db: Database session

    Raises:
        HTTPException: If group not found or user not authorized
    """
    try:
        group_uuid = uuid.UUID(group_id)
        current_user_uuid = uuid.UUID(current_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID format"
        )

    group = db.query(Group).filter(Group.id == group_uuid).first()

    if not group:
        logger.warning(f"Group {group_id} not found for deletion")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    # Authorization: User must be a member of the group
    if group.partner1_id != current_user_uuid and group.partner2_id != current_user_uuid:
        logger.warning(f"User {current_user_id} attempted to delete group {group_id} without authorization")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this group"
        )

    logger.warning(f"User {current_user_id} deleting group {group_id} and all associated data")
    db.delete(group)
    db.commit()

    logger.info(f"Group {group_id} deleted successfully")
