"""
Session API Routes
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db, SessionLocal
from app.db.models import Session as SessionModel, Message, Group, User
from app.services.session_service import SessionService
from app.services.notification_service import NotificationService, NotificationType
from app.schemas.schemas import SessionCreate, SessionResponse
from app.utils.auth import get_current_user
from app.utils.logger import get_logger
from typing import List
import asyncio
import uuid

logger = get_logger(__name__)

router = APIRouter()


def _http_error_from_value_error(error: ValueError) -> HTTPException:
    detail = str(error)
    if "not found" in detail.lower():
        return HTTPException(status_code=404, detail=detail)
    if "not authorized" in detail.lower():
        return HTTPException(status_code=403, detail=detail)
    return HTTPException(status_code=400, detail=detail)


def process_session_end_background(session_id: str) -> None:
    """
    Finish session extraction outside the HTTP request lifecycle.

    This runs in Starlette's threadpool because it is a sync background task.
    The service still exposes an async method, so use asyncio.run inside this
    isolated thread.
    """
    db = SessionLocal()
    try:
        result = asyncio.run(SessionService(db).process_ended_session(session_id))
        logger.info(f"Background session end processing completed: {result}")
    except Exception as e:
        logger.error(f"Background session end processing failed for {session_id}: {e}", exc_info=True)
    finally:
        db.close()


async def notify_joint_session_invite(
    db: Session,
    session: SessionModel,
    current_user_id: str
) -> None:
    """Best-effort partner notification for scheduled/waiting joint sessions."""
    if session.type != "joint" or session.status != "scheduled":
        return

    creator = db.query(User).filter(User.id == uuid.UUID(current_user_id)).first()
    creator_name = creator.name if creator else "Your partner"
    notification_service = NotificationService(db)

    for participant_id in session.participants:
        participant_id_str = str(participant_id)
        if participant_id_str == current_user_id:
            continue

        try:
            await notification_service.create_notification(
                user_id=participant_id_str,
                notification_type=NotificationType.JOINT_SESSION_INVITE,
                data={
                    "sessionId": str(session.id),
                    "groupId": str(session.group_id) if session.group_id else None,
                    "createdBy": current_user_id,
                    "createdByName": creator_name,
                    "scheduledFor": session.scheduled_for.isoformat() if session.scheduled_for else None,
                    "message": f"{creator_name} invited you to a joint session",
                },
            )
        except Exception as e:
            logger.warning(f"Failed to notify joint session invitee {participant_id}: {e}")


async def notify_partner_joined_session(
    db: Session,
    session: SessionModel,
    joined_user_id: str
) -> None:
    """Best-effort notification to the creator that the partner joined."""
    if not session.created_by or str(session.created_by) == joined_user_id:
        return

    joined_user = db.query(User).filter(User.id == uuid.UUID(joined_user_id)).first()
    joined_name = joined_user.name if joined_user else "Your partner"

    try:
        await NotificationService(db).create_notification(
            user_id=str(session.created_by),
            notification_type=NotificationType.PARTNER_JOINED_SESSION,
            data={
                "sessionId": str(session.id),
                "groupId": str(session.group_id) if session.group_id else None,
                "joinedBy": joined_user_id,
                "joinedByName": joined_name,
                "message": f"{joined_name} joined your joint session",
            },
        )
    except Exception as e:
        logger.warning(f"Failed to notify session creator {session.created_by}: {e}")


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

        if session_data.session_type == "private":
            session_data.participants = [current_user_id]
        elif group.status != "active" or not group.partner2_id:
            raise HTTPException(status_code=400, detail="Partner must accept the invite before joint sessions")
        else:
            partner_ids = {str(group.partner1_id), str(group.partner2_id)}
            if set(session_data.participants) != partner_ids:
                session_data.participants = list(partner_ids)

    try:
        service = SessionService(db)

        session = service.create_session(
            group_id=session_data.group_id,
            session_type=session_data.session_type,
            created_by=current_user_id,  # Authenticated user
            participants=session_data.participants,
            scheduled_for=session_data.scheduled_for,
            invite_message=session_data.invite_message
        )

        logger.info(f"Session created successfully: id={session.id}, type={session.type}, "
                    f"group_id={session.group_id}, status={session.status}")

        await notify_joint_session_invite(db, session, current_user_id)

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
    background_tasks: BackgroundTasks,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Second user confirms ending session.
    Triggers post-session processing.

    Requires authentication. User must be a participant in the session.
    """
    service = SessionService(db)
    try:
        result = await service.end_session(
            session_id,
            current_user_id,
            process_post_session=False
        )
    except ValueError as e:
        raise _http_error_from_value_error(e)

    if result.get("post_processing_required"):
        background_tasks.add_task(process_session_end_background, session_id)

    return result


@router.post("/{session_id}/join", response_model=SessionResponse)
async def join_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Join a scheduled joint session and activate the shared chat.
    """
    service = SessionService(db)
    try:
        session = service.join_session(session_id, current_user_id)
    except ValueError as e:
        raise _http_error_from_value_error(e)

    await notify_partner_joined_session(db, session, current_user_id)
    return session


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
