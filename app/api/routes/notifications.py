"""
Notification API Routes

Manages user notifications for check-ins, session events, etc.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.services.notification_service import NotificationService
from app.schemas.schemas import NotificationResponse
from app.utils.auth import get_current_user
from app.utils.logger import get_logger
from typing import List
from pydantic import BaseModel

router = APIRouter()
logger = get_logger(__name__)


class NotificationCountResponse(BaseModel):
    """Response for unread notification count."""
    unread_count: int

    class Config:
        populate_by_name = True


@router.get("/", response_model=List[NotificationResponse])
async def get_notifications(
    unread_only: bool = False,
    limit: int = 50,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get notifications for the current user.

    Query params:
    - unread_only: Only return unread notifications (default: false)
    - limit: Maximum number of notifications to return (default: 50)

    Returns:
        List of notifications, newest first
    """
    service = NotificationService(db)
    notifications = service.get_user_notifications(
        user_id=current_user_id,
        unread_only=unread_only,
        limit=limit
    )

    logger.info(f"Retrieved {len(notifications)} notifications for user {current_user_id}")

    # Convert to response format
    return [
        NotificationResponse(
            id=str(n.id),
            user_id=str(n.user_id),
            type=n.type,
            data=n.data,
            read=n.read,
            created_at=n.created_at
        )
        for n in notifications
    ]


@router.get("/count", response_model=NotificationCountResponse)
async def get_unread_count(
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get count of unread notifications.

    Returns:
        Object with unread_count field
    """
    service = NotificationService(db)
    count = service.get_unread_count(current_user_id)

    return NotificationCountResponse(unread_count=count)


@router.put("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mark a notification as read.

    Args:
        notification_id: UUID of the notification

    Returns:
        Success message
    """
    service = NotificationService(db)
    notification = service.mark_as_read(notification_id, current_user_id)

    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )

    return {"message": "Notification marked as read"}


@router.put("/read-all")
async def mark_all_read(
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mark all notifications as read.

    Returns:
        Count of notifications marked as read
    """
    service = NotificationService(db)
    count = service.mark_all_as_read(current_user_id)

    return {"message": f"Marked {count} notifications as read", "count": count}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a notification.

    Args:
        notification_id: UUID of the notification

    Returns:
        Success message
    """
    service = NotificationService(db)
    deleted = service.delete_notification(notification_id, current_user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )

    return {"message": "Notification deleted"}
