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


@router.get("/ws/test")
async def websocket_test():
    """
    Simple HTTP test endpoint to verify the /ws/ path is reachable.
    If this works but WebSocket doesn't, it's a Railway proxy issue.
    """
    logger.info("WebSocket test endpoint hit")
    return {
        "status": "ok",
        "message": "WebSocket path is reachable via HTTP",
        "websocket_url_format": "/ws/chat/{session_id}/{user_id}?token=JWT"
    }


@router.websocket("/ws/echo")
async def websocket_echo(websocket: WebSocket):
    """
    No-auth WebSocket echo endpoint for debugging Railway connectivity.

    If this works but /ws/chat doesn't, the issue is auth/path-related.
    If this also fails with 403, it's a Railway edge configuration issue.

    Remove this endpoint after debugging is complete.
    """
    logger.info("====== WEBSOCKET ECHO ENDPOINT HIT ======")
    await websocket.accept()
    logger.info("WebSocket echo: connection accepted")

    try:
        await websocket.send_json({
            "type": "connected",
            "message": "Echo endpoint connected successfully"
        })

        while True:
            data = await websocket.receive_text()
            logger.info(f"WebSocket echo received: {data}")
            await websocket.send_text(f"Echo: {data}")
    except Exception as e:
        logger.info(f"WebSocket echo disconnected: {e}")


@router.websocket("/ws/chat/{session_id}/{user_id}")
async def websocket_chat(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for chat sessions.

    Requires JWT authentication via query parameter.
    Connection URL: ws://localhost:8000/ws/chat/{sessionId}/{userId}?token=JWT_TOKEN

    The user_id in the path must match the user_id from the JWT token.
    This dual authentication provides both URL-based routing and secure verification.

    Protocol:
    - Client connects with session_id, user_id, and JWT token
    - Server validates token and verifies user_id matches
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
        "sessionStatus": "active",
        "messages": [...]
    }
    {
        "type": "message",
        "messageId": "uuid",
        "senderId": "uuid",
        "senderName": "John",
        "content": "Message text",
        "timestamp": "2024-01-01T12:00:00",
        "sequenceNumber": 1
    }
    {
        "type": "typing",
        "userId": "uuid",
        "isTyping": true
    }
    {
        "type": "error",
        "message": "Error description"
    }
    """
    logger.info(f"====== WEBSOCKET ENDPOINT HIT ======")
    logger.info(f">>> WebSocket connection attempt: session={session_id}, user={user_id}")
    logger.info(f">>> Token length: {len(token) if token else 0}")

    # Validate JWT token and verify user_id matches
    try:
        token_user_id = get_user_from_token(token)
        # Verify the user_id in path matches the one in token
        if token_user_id != user_id:
            logger.warning(f"User ID mismatch: path={user_id}, token={token_user_id}")
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="User ID does not match token"
            )
        logger.info(f"WebSocket authentication successful for user {user_id}")
    except WebSocketException:
        raise
    except Exception as e:
        logger.warning(f"WebSocket authentication failed: {str(e)}")
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired authentication token"
        )

    handler = ChatHandler(db)
    await handler.handle_connection(websocket, session_id, user_id)
