"""
CheckIn API Routes
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.services.checkin_service import CheckInService
from app.schemas.schemas import (
    CheckInResponse,
    CheckInApproval,
    CheckInVerification
)
from app.utils.auth import get_current_user
from typing import List

router = APIRouter()


@router.get("/{session_id}/proposed", response_model=List[CheckInResponse])
async def get_proposed_checkins(
    session_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all proposed check-ins for a session.

    Requires authentication. User must be a participant in the session.
    Called after session ends to show check-ins needing approval.
    """
    service = CheckInService(db)
    checkins = service.get_proposed_checkins_for_session(session_id)
    return checkins


@router.get("/{group_id}/active", response_model=List[CheckInResponse])
async def get_active_checkins(
    group_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get active check-ins for the current authenticated user in a group.

    Requires authentication.
    Returns check-ins where:
    - Status is 'active'
    - User is assigned_to OR verifier_id
    """
    service = CheckInService(db)
    checkins = service.get_active_checkins_for_user(group_id, current_user_id)
    return checkins


@router.put("/{checkin_id}/approve")
async def approve_checkin(
    checkin_id: str,
    approval_data: CheckInApproval,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Approve a proposed check-in.

    Requires authentication.

    Args:
        checkin_id: UUID of check-in
        approval_data: Contains role ('assigned' or 'verifier')
        current_user_id: Authenticated user ID from JWT

    Check-in becomes active when both parties approve.
    Sends notification to assigned user when fully approved.
    """
    service = CheckInService(db)

    try:
        checkin = await service.approve_checkin(
            checkin_id=checkin_id,
            user_id=current_user_id,
            role=approval_data.role
        )

        return {
            "success": True,
            "checkin": checkin,
            "message": f"Check-in approved by {approval_data.role}"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{checkin_id}/mark-done")
async def mark_checkin_done(
    checkin_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mark a check-in occurrence as done.

    Requires authentication.
    Increments progress counter.
    If requires_verification, sets status to 'awaiting_verification'
    and notifies the verifier.
    """
    service = CheckInService(db)

    try:
        checkin = await service.mark_checkin_done(checkin_id, current_user_id)

        return {
            "success": True,
            "checkin": checkin,
            "message": "Check-in marked as done"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{checkin_id}/verify")
async def verify_checkin(
    checkin_id: str,
    verification_data: CheckInVerification,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verify or reject a completed check-in.

    Requires authentication.

    Args:
        checkin_id: UUID of check-in
        verification_data: Contains status ('verified' or 'needs_work') and optional feedback
        current_user_id: Authenticated user ID from JWT (verifier)

    If verified, check-in returns to 'active' status and user is notified.
    If needs_work, assigned user must redo it and is notified.
    """
    service = CheckInService(db)

    try:
        checkin = await service.verify_checkin(
            checkin_id=checkin_id,
            verified_by=current_user_id,
            status=verification_data.status,
            feedback=verification_data.feedback
        )

        return {
            "success": True,
            "checkin": checkin,
            "message": f"Check-in {verification_data.status}"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{checkin_id}", response_model=CheckInResponse)
async def get_checkin(
    checkin_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get check-in by ID.

    Requires authentication. User must be assigned_to or verifier_id.
    """
    service = CheckInService(db)
    checkin = service.get_checkin(checkin_id)

    if not checkin:
        raise HTTPException(status_code=404, detail="Check-in not found")

    # Authorization: User must be involved in this check-in
    import uuid
    current_user_uuid = uuid.UUID(current_user_id)

    if checkin.assigned_to != current_user_uuid and checkin.verifier_id != current_user_uuid:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to access this check-in"
        )

    return checkin
