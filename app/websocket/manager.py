"""
WebSocket Connection Manager

Manages active WebSocket connections for real-time communication.
Tracks connections per session and enables broadcasting.
"""

from fastapi import WebSocket
from typing import Dict, List, Set, Optional
from app.utils.logger import get_logger
import json

logger = get_logger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections.

    Responsibilities:
    - Track active connections per session
    - Track connections per user (for notifications)
    - Allow multiple connections per user (multi-device)
    - Broadcast messages to all connections in a session
    - Send notifications to specific users
    - Handle connection lifecycle
    """

    def __init__(self):
        # session_id -> List[WebSocket]
        self.active_connections: Dict[str, List[WebSocket]] = {}

        # Track which user each connection belongs to
        # websocket -> user_id
        self.connection_users: Dict[WebSocket, str] = {}

        # Track which session each connection belongs to
        # websocket -> session_id
        self.connection_sessions: Dict[WebSocket, str] = {}

        # Track all connections for a user (across sessions)
        # user_id -> Set[WebSocket]
        self.user_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(
        self,
        session_id: str,
        user_id: str,
        websocket: WebSocket,
        subprotocol: Optional[str] = None
    ):
        """
        Accept and register a new WebSocket connection.

        Args:
            session_id: UUID of the session
            user_id: UUID of the user
            websocket: WebSocket connection object
        """
        logger.info(f"manager.connect: Accepting WebSocket for user={user_id}")
        if subprotocol:
            await websocket.accept(subprotocol=subprotocol)
        else:
            await websocket.accept()
        logger.info(f"manager.connect: WebSocket accepted")

        # Send immediate ping to verify connection
        try:
            await websocket.send_json({"type": "ping", "message": "connection_test"})
            logger.info(f"manager.connect: Ping sent successfully")
        except Exception as e:
            logger.error(f"manager.connect: Failed to send ping - {e}")

        # Add to session's connections
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []

        self.active_connections[session_id].append(websocket)
        self.connection_users[websocket] = user_id
        self.connection_sessions[websocket] = session_id

        # Track user's connections (for notifications)
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(websocket)

        logger.info(f"manager.connect: User {user_id} fully connected to session {session_id}")

    def disconnect(self, session_id: str, websocket: WebSocket):
        """
        Remove a WebSocket connection.

        Args:
            session_id: UUID of the session
            websocket: WebSocket connection object
        """
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)

            # Clean up empty session
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

        # Remove from user tracking
        if websocket in self.connection_users:
            user_id = self.connection_users[websocket]
            del self.connection_users[websocket]

            # Remove from user's connection set
            if user_id in self.user_connections:
                self.user_connections[user_id].discard(websocket)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]

            logger.info(f"User {user_id} disconnected from session {session_id}")

        # Remove session tracking
        if websocket in self.connection_sessions:
            del self.connection_sessions[websocket]

    async def send_personal_message(
        self,
        message: dict,
        websocket: WebSocket
    ):
        """
        Send message to a specific connection.

        Args:
            message: Dictionary to send as JSON
            websocket: Target WebSocket connection
        """
        try:
            logger.info(f"send_personal_message: Sending message type={message.get('type')}")
            await websocket.send_json(message)
            logger.info(f"send_personal_message: Message sent successfully")
        except Exception as e:
            logger.error(f"send_personal_message: Error - {e}", exc_info=True)

    async def broadcast(
        self,
        session_id: str,
        message: dict,
        exclude_websocket: WebSocket = None
    ):
        """
        Broadcast message to all connections in a session.

        Args:
            session_id: UUID of the session
            message: Dictionary to send as JSON
            exclude_websocket: Optional connection to exclude (e.g., sender)
        """
        if session_id not in self.active_connections:
            return

        disconnected = []

        for connection in self.active_connections[session_id]:
            if connection == exclude_websocket:
                continue

            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to connection: {e}")
                disconnected.append(connection)

        # Clean up failed connections
        for conn in disconnected:
            self.disconnect(session_id, conn)

    async def broadcast_to_user(
        self,
        user_id: str,
        message: dict
    ):
        """
        Broadcast message to all of a user's connections (across all sessions).

        Used for sending notifications to a user regardless of which session
        they're in.

        Args:
            user_id: UUID of the user
            message: Dictionary to send as JSON
        """
        if user_id not in self.user_connections:
            logger.debug(f"User {user_id} has no active connections")
            return

        disconnected = []

        for connection in self.user_connections[user_id]:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to user {user_id}: {e}")
                disconnected.append(connection)

        # Clean up failed connections
        for conn in disconnected:
            session_id = self.connection_sessions.get(conn)
            if session_id:
                self.disconnect(session_id, conn)

    async def broadcast_typing(
        self,
        session_id: str,
        user_id: str,
        is_typing: bool
    ):
        """
        Broadcast typing indicator to other users in session.

        Uses camelCase for frontend compatibility.

        Args:
            session_id: UUID of the session
            user_id: UUID of the user typing
            is_typing: True if started typing, False if stopped
        """
        message = {
            'type': 'typing',
            'userId': user_id,
            'isTyping': is_typing
        }

        await self.broadcast(session_id, message)

    def get_session_connections(self, session_id: str) -> List[WebSocket]:
        """Get all active connections for a session."""
        return self.active_connections.get(session_id, [])

    def get_connection_count(self, session_id: str) -> int:
        """Get number of active connections in a session."""
        return len(self.get_session_connections(session_id))

    def is_user_connected(self, user_id: str) -> bool:
        """Check if user has any active connections."""
        return user_id in self.user_connections and len(self.user_connections[user_id]) > 0

    def get_user_connection_count(self, user_id: str) -> int:
        """Get number of active connections for a user."""
        return len(self.user_connections.get(user_id, set()))


# Global connection manager instance
manager = ConnectionManager()
