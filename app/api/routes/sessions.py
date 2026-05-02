"""
Session API Routes
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Session as SessionModel, Message, Group
from app.services.session_service import SessionService
from app.schemas.schemas import SessionCreate, SessionResponse
from app.utils.auth import get_current_user
from app.utils.logger import get_logger
from typing import List
import uuid

logger = get_logger(__name__)

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

    Private sessions can be created without a group_id (solo sessions).
    Joint sessions require a group_id (partner relationship).
    """
    logger.info(f"Session creation request: type={session_data.session_type}, "
                f"group_id={session_data.group_id}, "
                f"user={current_user_id}, "
                f"participants={session_data.participants}")

    if session_data.session_type not in {"private", "joint"}:
        raise HTTPException(
            status_code=400,
            detail="sessionType must be 'private' or 'joint'"
        )

    # Joint sessions require a group/relationship
    if session_data.session_type == "joint" and not session_data.group_id:
        logger.warning(f"Session creation failed: Joint session without group_id")
        raise HTTPException(
            status_code=400,
            detail="Group ID is required for joint sessions"
        )

    if current_user_id not in session_data.participants:
        session_data.participants.append(current_user_id)

    if session_data.group_id:
        try:
            group_uuid = uuid.UUID(session_data.group_id)
            current_user_uuid = uuid.UUID(current_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid group or user ID")

        group = db.query(Group).filter(Group.id == group_uuid).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        if group.partner1_id != current_user_uuid and group.partner2_id != current_user_uuid:
            raise HTTPException(status_code=403, detail="Not authorized for this group")

        partner_ids = {str(group.partner1_id), str(group.partner2_id)}
        if session_data.session_type == "private":
            session_data.participants = [current_user_id]
        elif set(session_data.participants) != partner_ids:
            session_data.participants = list(partner_ids)

    try:
        service = SessionService(db)

        session = service.create_session(
            group_id=session_data.group_id,
            session_type=session_data.session_type,
            created_by=current_user_id,  # Authenticated user
            participants=session_data.participants,
            scheduled_for=session_data.scheduled_for
        )

        logger.info(f"Session created successfully: id={session.id}, type={session.type}, "
                    f"group_id={session.group_id}, status={session.status}")

        return session

    except Exception as e:
        logger.error(f"Session creation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create session: {str(e)}"
        )


@router.get("/my", response_model=List[SessionResponse])
async def get_my_sessions(
    session_type: str = None,
    status: str = None,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List sessions where current user is a participant.
    """
    current_user_uuid = uuid.UUID(current_user_id)
    query = db.query(SessionModel).filter(
        SessionModel.participants.any(current_user_uuid)
    )

    if session_type:
        query = query.filter(SessionModel.type == session_type)
    if status:
        query = query.filter(SessionModel.status == status)

    sessions = query.order_by(SessionModel.created_at.desc()).all()
    return sessions


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
    current_user_uuid = uuid.UUID(current_user_id)

    if current_user_uuid not in session.participants:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to access this session"
        )

    return session


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = 200,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get chronological message transcript for a session.
    """
    session = db.query(SessionModel).filter(
        SessionModel.id == uuid.UUID(session_id)
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    current_user_uuid = uuid.UUID(current_user_id)
    if current_user_uuid not in session.participants:
        raise HTTPException(status_code=403, detail="Not authorized to access this session")

    msgs = (
        db.query(Message)
        .filter(Message.session_id == uuid.UUID(session_id))
        .order_by(Message.sequence_number.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    msgs.reverse()

    return {
        "sessionId": session_id,
        "messages": [
            {
                "messageId": str(m.id),
                "senderId": m.sender_id,
                "senderName": m.sender_name,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
                "sequenceNumber": m.sequence_number,
                "metadata": m.message_metadata
            }
            for m in msgs
        ]
    }
