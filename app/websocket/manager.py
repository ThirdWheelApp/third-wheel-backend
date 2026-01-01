"""
WebSocket Connection Manager

Manages active WebSocket connections for real-time communication.
Tracks connections per session and enables broadcasting.
"""

from fastapi import WebSocket
from typing import Dict, List, Set
import json


class ConnectionManager:
    """
    Manages WebSocket connections.

    Responsibilities:
    - Track active connections per session
    - Allow multiple connections per user (multi-device)
    - Broadcast messages to all connections in a session
    - Handle connection lifecycle
    """

    def __init__(self):
        # session_id -> List[WebSocket]
        self.active_connections: Dict[str, List[WebSocket]] = {}

        # Track which user each connection belongs to
        # websocket -> user_id
        self.connection_users: Dict[WebSocket, str] = {}

    async def connect(
        self,
        session_id: str,
        user_id: str,
        websocket: WebSocket
    ):
        """
        Accept and register a new WebSocket connection.

        Args:
            session_id: UUID of the session
            user_id: UUID of the user
            websocket: WebSocket connection object
        """
        await websocket.accept()

        # Add to session's connections
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []

        self.active_connections[session_id].append(websocket)
        self.connection_users[websocket] = user_id

        print(f"User {user_id} connected to session {session_id}")

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
            print(f"User {user_id} disconnected from session {session_id}")

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
            await websocket.send_json(message)
        except Exception as e:
            print(f"Error sending personal message: {e}")

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
                print(f"Error broadcasting to connection: {e}")
                disconnected.append(connection)

        # Clean up failed connections
        for conn in disconnected:
            self.disconnect(session_id, conn)

    async def broadcast_typing(
        self,
        session_id: str,
        user_id: str,
        is_typing: bool
    ):
        """
        Broadcast typing indicator to other users in session.

        Args:
            session_id: UUID of the session
            user_id: UUID of the user typing
            is_typing: True if started typing, False if stopped
        """
        message = {
            'type': 'typing',
            'user_id': user_id,
            'is_typing': is_typing
        }

        await self.broadcast(session_id, message)

    def get_session_connections(self, session_id: str) -> List[WebSocket]:
        """Get all active connections for a session."""
        return self.active_connections.get(session_id, [])

    def get_connection_count(self, session_id: str) -> int:
        """Get number of active connections in a session."""
        return len(self.get_session_connections(session_id))


# Global connection manager instance
manager = ConnectionManager()
