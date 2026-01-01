"""
Session API Routes
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.services.session_service import SessionService
from app.schemas.schemas import SessionCreate, SessionResponse
from app.utils.auth import get_current_user

router = APIRouter()


@router.post("/", response_model=SessionResponse)
async def create_session(
    session_data: SessionCreate,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new therapy session.

    Requires authentication. Current user will be set as the session creator
    and must be included in the participants list.
    """
    service = SessionService(db)

    session = service.create_session(
        group_id=session_data.group_id,
        session_type=session_data.session_type,
        created_by=current_user_id,  # Authenticated user
        participants=session_data.participants,
        scheduled_for=session_data.scheduled_for
    )

    return session


@router.post("/{session_id}/request-end")
async def request_end_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    First user requests to end session.

    Requires authentication. User must be a participant in the session.
    """
    service = SessionService(db)
    result = await service.request_end_session(session_id, current_user_id)
    return result


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Second user confirms ending session.
    Triggers post-session processing.

    Requires authentication. User must be a participant in the session.
    """
    service = SessionService(db)
    result = await service.end_session(session_id, current_user_id)
    return result


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get session by ID.

    Requires authentication. User must be a participant in the session.
    """
    service = SessionService(db)
    session = service.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Authorization: User must be a participant in the session
    # Convert current_user_id to UUID for comparison
    import uuid
    current_user_uuid = uuid.UUID(current_user_id)

    if current_user_uuid not in session.participants:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to access this session"
        )

    return session
