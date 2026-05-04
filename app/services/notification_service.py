"""
Notification Service

Handles creation, retrieval, and management of user notifications.
Supports real-time delivery via WebSocket.
"""

from sqlalchemy.orm import Session
from app.db.models import Notification, User
from app.websocket.manager import manager
from app.utils.logger import get_logger
from app.utils.serializers import convert_keys_to_camel
from typing import List, Dict, Optional
import uuid
from datetime import datetime

logger = get_logger(__name__)


# Notification type constants
class NotificationType:
    """Notification type identifiers for different events."""
    CHECK_IN_ASSIGNED = "check_in_assigned"
    CHECK_IN_VERIFICATION_NEEDED = "check_in_verification_needed"
    CHECK_IN_APPROVED = "check_in_approved"
    CHECK_IN_VERIFIED = "check_in_verified"
    CHECK_IN_NEEDS_WORK = "check_in_needs_work"
    SESSION_END_REQUESTED = "session_end_requested"
    POST_SESSION_ACTIONS = "post_session_actions"
    JOINT_SESSION_INVITE = "joint_session_invite"
    PARTNER_JOINED_SESSION = "partner_joined_session"
    SESSION_STARTED = "session_started"


class NotificationService:
    """
    Service for managing user notifications.

    Responsibilities:
    - Create notifications for various events
    - Retrieve user's notifications
    - Mark notifications as read
    - Broadcast notifications via WebSocket
    """

    def __init__(self, db: Session):
        self.db = db

    async def create_notification(
        self,
        user_id: str,
        notification_type: str,
        data: Dict,
        broadcast: bool = True
    ) -> Notification:
        """
        Create a new notification for a user.

        Args:
            user_id: UUID of the user to notify
            notification_type: Type of notification (from NotificationType)
            data: Additional data for the notification
            broadcast: Whether to broadcast via WebSocket

        Returns:
            Created Notification object
        """
        notification = Notification(
            id=uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            type=notification_type,
            data=data,
            read=False,
            created_at=datetime.utcnow()
        )

        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)

        logger.info(f"Created notification {notification.id} for user {user_id}: {notification_type}")

        # Broadcast via WebSocket if user is connected
        if broadcast:
            await self._broadcast_notification(user_id, notification)

        return notification

    async def _broadcast_notification(self, user_id: str, notification: Notification):
        """
        Broadcast notification to user via WebSocket if connected.

        Uses the session-based connection manager to find user's connection.
        """
        try:
            # Convert notification to dict with camelCase
            notification_data = {
                'type': 'notification',
                'notification': convert_keys_to_camel({
                    'id': str(notification.id),
                    'type': notification.type,
                    'data': notification.data,
                    'read': notification.read,
                    'created_at': notification.created_at.isoformat()
                })
            }

            # Try to send to all sessions where user might be connected
            # This is a simplified approach - in production, you'd have
            # a user-to-websocket mapping
            await manager.broadcast_to_user(user_id, notification_data)

        except Exception as e:
            logger.warning(f"Failed to broadcast notification to user {user_id}: {e}")

    def get_user_notifications(
        self,
        user_id: str,
        unread_only: bool = False,
        limit: int = 50
    ) -> List[Notification]:
        """
        Get notifications for a user.

        Args:
            user_id: UUID of the user
            unread_only: Only return unread notifications
            limit: Maximum number to return

        Returns:
            List of Notification objects
        """
        query = self.db.query(Notification).filter(
            Notification.user_id == uuid.UUID(user_id)
        )

        if unread_only:
            query = query.filter(Notification.read == False)

        return query.order_by(Notification.created_at.desc()).limit(limit).all()

    def get_unread_count(self, user_id: str) -> int:
        """Get count of unread notifications for user."""
        return self.db.query(Notification).filter(
            Notification.user_id == uuid.UUID(user_id),
            Notification.read == False
        ).count()

    def mark_as_read(self, notification_id: str, user_id: str) -> Optional[Notification]:
        """
        Mark a notification as read.

        Args:
            notification_id: UUID of the notification
            user_id: UUID of the user (for authorization)

        Returns:
            Updated notification or None if not found/unauthorized
        """
        notification = self.db.query(Notification).filter(
            Notification.id == uuid.UUID(notification_id),
            Notification.user_id == uuid.UUID(user_id)
        ).first()

        if notification:
            notification.read = True
            self.db.commit()
            logger.info(f"Marked notification {notification_id} as read")

        return notification

    def mark_all_as_read(self, user_id: str) -> int:
        """
        Mark all notifications as read for a user.

        Returns:
            Number of notifications marked as read
        """
        result = self.db.query(Notification).filter(
            Notification.user_id == uuid.UUID(user_id),
            Notification.read == False
        ).update({"read": True})

        self.db.commit()
        logger.info(f"Marked {result} notifications as read for user {user_id}")

        return result

    def delete_notification(self, notification_id: str, user_id: str) -> bool:
        """
        Delete a notification.

        Args:
            notification_id: UUID of the notification
            user_id: UUID of the user (for authorization)

        Returns:
            True if deleted, False if not found/unauthorized
        """
        notification = self.db.query(Notification).filter(
            Notification.id == uuid.UUID(notification_id),
            Notification.user_id == uuid.UUID(user_id)
        ).first()

        if notification:
            self.db.delete(notification)
            self.db.commit()
            logger.info(f"Deleted notification {notification_id}")
            return True

        return False

    # ========================================================================
    # Convenience methods for creating specific notification types
    # ========================================================================

    async def notify_check_in_assigned(
        self,
        user_id: str,
        check_in_id: str,
        title: str,
        assigned_by_name: str
    ):
        """Notify user that a new check-in has been assigned to them."""
        return await self.create_notification(
            user_id=user_id,
            notification_type=NotificationType.CHECK_IN_ASSIGNED,
            data={
                'checkInId': check_in_id,
                'title': title,
                'assignedByName': assigned_by_name,
                'message': f"New check-in: {title}"
            }
        )

    async def notify_check_in_verification_needed(
        self,
        verifier_id: str,
        check_in_id: str,
        title: str,
        completed_by_name: str
    ):
        """Notify verifier that check-in needs verification."""
        return await self.create_notification(
            user_id=verifier_id,
            notification_type=NotificationType.CHECK_IN_VERIFICATION_NEEDED,
            data={
                'checkInId': check_in_id,
                'title': title,
                'completedByName': completed_by_name,
                'message': f"{completed_by_name} completed: {title}"
            }
        )

    async def notify_session_end_requested(
        self,
        user_id: str,
        session_id: str,
        requested_by_name: str
    ):
        """Notify user that partner wants to end session."""
        return await self.create_notification(
            user_id=user_id,
            notification_type=NotificationType.SESSION_END_REQUESTED,
            data={
                'sessionId': session_id,
                'requestedByName': requested_by_name,
                'message': f"{requested_by_name} wants to end the session"
            }
        )

    async def notify_post_session_actions(
        self,
        user_id: str,
        session_id: str,
        pending_count: int
    ):
        """Notify user about pending post-session actions."""
        return await self.create_notification(
            user_id=user_id,
            notification_type=NotificationType.POST_SESSION_ACTIONS,
            data={
                'sessionId': session_id,
                'pendingCount': pending_count,
                'message': f"You have {pending_count} pending action(s) from your session"
            }
        )
