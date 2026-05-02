"""
WebSocket API Routes

Handles WebSocket connections for real-time chat.
"""

from fastapi import APIRouter, WebSocket, Depends, Query, status
from fastapi.exceptions import WebSocketException
from starlette.websockets import WebSocketState
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.websocket.chat_handler import ChatHandler
from app.utils.auth import get_user_from_token
from app.utils.logger import get_logger
from typing import Optional, List
import uuid

router = APIRouter()
logger = get_logger(__name__)


def _get_subprotocols(websocket: WebSocket) -> List[str]:
    header_value = websocket.headers.get("sec-websocket-protocol")
    if not header_value:
        return []
    return [part.strip() for part in header_value.split(",") if part.strip()]


def _get_token_from_subprotocol(websocket: WebSocket) -> Optional[str]:
    protocols = _get_subprotocols(websocket)
    if "jwt" not in protocols:
        return None
    for protocol in protocols:
        if protocol != "jwt":
            return protocol
    return None


def _select_subprotocol(websocket: WebSocket) -> Optional[str]:
    return "jwt" if "jwt" in _get_subprotocols(websocket) else None


async def _accept_websocket(websocket: WebSocket, subprotocol: Optional[str]) -> None:
    if getattr(websocket, "application_state", None) == WebSocketState.CONNECTED:
        logger.info("WebSocket already accepted; skipping accept.")
        return
    if subprotocol:
        await websocket.accept(subprotocol=subprotocol)
    else:
        await websocket.accept()


def _get_token_from_headers(websocket: WebSocket) -> Optional[str]:
    auth_header = websocket.headers.get("authorization")
    if not auth_header:
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def _resolve_token(websocket: WebSocket, token: Optional[str]) -> Optional[str]:
    header_token = _get_token_from_headers(websocket)
    subprotocol_token = _get_token_from_subprotocol(websocket)
    return header_token or subprotocol_token or token


async def _handle_websocket_chat(
    websocket: WebSocket,
    session_id: str,
    user_id: Optional[str],
    token: Optional[str],
    db: Session,
):
    selected_subprotocol = _select_subprotocol(websocket)
    await _accept_websocket(websocket, selected_subprotocol)

    token_value = _resolve_token(websocket, token)
    header_token = _get_token_from_headers(websocket)
    subprotocol_token = _get_token_from_subprotocol(websocket)
    if token_value and token_value == header_token:
        token_source = "header"
    elif token_value and token_value == subprotocol_token:
        token_source = "subprotocol"
    else:
        token_source = "query"
    logger.info(f">>> WebSocket token source: {token_source}, length: {len(token_value) if token_value else 0}")

    if not token_value:
        logger.warning("WebSocket authentication failed: missing token")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing authentication token",
        )
        return

    # Validate JWT token and verify user_id matches (if provided)
    try:
        token_user_id = get_user_from_token(token_value)
        if user_id and token_user_id != user_id:
            logger.warning(f"User ID mismatch: path={user_id}, token={token_user_id}")
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="User ID does not match token",
            )
            return
        logger.info(f"WebSocket authentication successful for user {token_user_id}")
    except WebSocketException:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired authentication token",
        )
        return
    except Exception as e:
        logger.warning(f"WebSocket authentication failed: {str(e)}")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired authentication token",
        )
        return

    try:
        from app.db.models import Session as SessionModel

        session = db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()
        if not session:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Session not found",
            )
            return
        if uuid.UUID(token_user_id) not in session.participants:
            logger.warning(f"User {token_user_id} attempted WebSocket access to session {session_id}")
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Not authorized for this session",
            )
            return
    except ValueError:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid session or user ID",
        )
        return

    handler = ChatHandler(db)
    await handler.handle_connection(websocket, session_id, token_user_id, selected_subprotocol)


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


@router.websocket("/ws/chat/{session_id}")
async def websocket_chat_session(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for chat sessions (token-only path).

    Connection URL:
    ws://localhost:8000/ws/chat/{sessionId}?token=JWT_TOKEN

    Token can also be provided via Authorization header:
    Authorization: Bearer <JWT>
    Or via subprotocol: new WebSocket(url, ['jwt', token])
    """
    logger.info("====== WEBSOCKET ENDPOINT HIT ======")
    logger.info(f">>> WebSocket connection attempt: session={session_id}")

    await _handle_websocket_chat(websocket, session_id, None, token, db)


@router.websocket("/ws/chat/{session_id}/{user_id}")
async def websocket_chat(
    websocket: WebSocket,
    session_id: str,
    user_id: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for chat sessions.

    Requires JWT authentication via query parameter or Authorization header.
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
    logger.info("====== WEBSOCKET ENDPOINT HIT ======")
    logger.info(f">>> WebSocket connection attempt: session={session_id}, user={user_id}")

    await _handle_websocket_chat(websocket, session_id, user_id, token, db)
