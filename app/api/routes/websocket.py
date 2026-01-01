"""
WebSocket API Routes

Handles WebSocket connections for real-time chat.
"""

from fastapi import APIRouter, WebSocket, Depends, Query, status
from fastapi.exceptions import WebSocketException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.websocket.chat_handler import ChatHandler
from app.utils.auth import get_user_from_token
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.websocket("/ws/chat/{session_id}")
async def websocket_chat(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for chat sessions.

    Requires JWT authentication via query parameter.
    Connection URL: ws://localhost:8000/ws/chat/{session_id}?token=JWT_TOKEN

    Protocol:
    - Client connects with session_id and JWT token
    - Server validates token and extracts user_id
    - Server sends 'sync' message with current session state
    - Client sends messages with type: 'message', 'typing_start', 'typing_stop'
    - Server broadcasts responses to all connections in session

    Message Types (Client -> Server):
    {
        "type": "message",
        "content": "Hello, this is my message"
    }
    {
        "type": "typing_start"
    }
    {
        "type": "typing_stop"
    }

    Message Types (Server -> Client):
    {
        "type": "sync",
        "session_status": "active",
        "messages": [...]
    }
    {
        "type": "message",
        "message_id": "uuid",
        "sender_id": "uuid",
        "sender_name": "John",
        "content": "Message text",
        "timestamp": "2024-01-01T12:00:00",
        "sequence_number": 1
    }
    {
        "type": "typing",
        "user_id": "uuid",
        "is_typing": true
    }
    {
        "type": "error",
        "message": "Error description"
    }
    """
    # Validate JWT token and extract user_id before accepting connection
    try:
        user_id = get_user_from_token(token)
        logger.info(f"WebSocket authentication successful for user {user_id}")
    except Exception as e:
        logger.warning(f"WebSocket authentication failed: {str(e)}")
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired authentication token"
        )

    handler = ChatHandler(db)
    await handler.handle_connection(websocket, session_id, user_id)
