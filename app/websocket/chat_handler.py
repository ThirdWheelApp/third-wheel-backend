"""
Chat WebSocket Handler

Handles WebSocket messages for chat sessions.
Coordinates with ChatService and broadcasts responses.

All WebSocket messages use camelCase for frontend compatibility.
"""

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from app.websocket.manager import manager
from app.services.chat_service import ChatService
from app.utils.message_queue import session_message_queue
from app.utils.serializers import convert_keys_to_camel
from app.utils.logger import get_logger
import json

logger = get_logger(__name__)


class ChatHandler:
    """
    Handles WebSocket communication for chat sessions.

    Responsibilities:
    - Process incoming messages
    - Coordinate with ChatService
    - Broadcast responses and typing indicators
    - Handle connection lifecycle
    """

    def __init__(self, db: Session):
        self.db = db
        self.chat_service = ChatService(db)

    async def handle_connection(
        self,
        websocket: WebSocket,
        session_id: str,
        user_id: str
    ):
        """
        Handle a WebSocket connection for the duration of the session.

        Args:
            websocket: WebSocket connection
            session_id: UUID of the session
            user_id: UUID of the user
        """
        logger.info(f"handle_connection: Starting for session={session_id}, user={user_id}")

        # Connect
        await manager.connect(session_id, user_id, websocket)
        logger.info(f"handle_connection: WebSocket accepted and registered")

        try:
            # Send sync message with current session state
            logger.info(f"handle_connection: Sending sync message...")
            await self._send_sync_message(websocket, session_id)
            logger.info(f"handle_connection: Sync message sent successfully")

            # Listen for messages
            logger.info(f"handle_connection: Entering message loop...")
            while True:
                data = await websocket.receive_json()
                await self._handle_message(
                    data,
                    session_id,
                    user_id,
                    websocket
                )

        except WebSocketDisconnect:
            logger.info(f"handle_connection: WebSocketDisconnect received")
            manager.disconnect(session_id, websocket)
        except Exception as e:
            logger.error(f"handle_connection: Exception - {e}", exc_info=True)
            manager.disconnect(session_id, websocket)

    async def _handle_message(
        self,
        data: dict,
        session_id: str,
        user_id: str,
        websocket: WebSocket
    ):
        """
        Route incoming message to appropriate handler.

        Args:
            data: Message data from client
            session_id: UUID of the session
            user_id: UUID of the user
            websocket: WebSocket connection
        """
        message_type = data.get('type')

        if message_type == 'message':
            # Enqueue message for processing (prevents race conditions)
            await session_message_queue.enqueue(
                session_id,
                data,
                lambda msg_data: self._process_user_message(
                    msg_data,
                    session_id,
                    user_id,
                    websocket
                )
            )

        elif message_type == 'typing_start':
            await manager.broadcast_typing(session_id, user_id, True)

        elif message_type == 'typing_stop':
            await manager.broadcast_typing(session_id, user_id, False)

    async def _process_user_message(
        self,
        data: dict,
        session_id: str,
        user_id: str,
        websocket: WebSocket
    ):
        """
        Process a user message and get AI response.

        Supports both streaming and non-streaming modes.
        Client can request streaming by setting 'stream': true in message.

        Args:
            data: Message data (may include 'stream': true for streaming)
            session_id: UUID of the session
            user_id: UUID of the user
            websocket: WebSocket connection
        """
        content = data.get('content', '')
        use_streaming = data.get('stream', False)

        if not content.strip():
            return

        try:
            # Broadcast that AI is typing (using camelCase for frontend)
            await manager.broadcast(
                session_id,
                {
                    'type': 'typing',
                    'userId': 'therapist',
                    'isTyping': True
                }
            )

            # Get session to determine type
            from app.db.models import Session as SessionModel
            import uuid

            session = self.db.query(SessionModel).filter(
                SessionModel.id == uuid.UUID(session_id)
            ).first()

            if not session:
                await manager.send_personal_message(
                    {'type': 'error', 'message': 'Session not found'},
                    websocket
                )
                return

            # Handle streaming for private sessions
            if session.type == 'private' and use_streaming:
                await self._process_streaming_message(
                    session_id,
                    user_id,
                    content,
                    websocket
                )
            elif session.type == 'private':
                response = await self.chat_service.process_private_message(
                    session_id,
                    user_id,
                    content
                )
                await self._broadcast_response(session_id, response)
            else:  # joint (streaming not yet supported for joint due to LangGraph complexity)
                response = await self.chat_service.process_joint_message(
                    session_id,
                    user_id,
                    content
                )
                await self._broadcast_response(session_id, response)

            # Stop typing indicator
            await manager.broadcast(
                session_id,
                {
                    'type': 'typing',
                    'userId': 'therapist',
                    'isTyping': False
                }
            )

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            await manager.broadcast(
                session_id,
                {
                    'type': 'error',
                    'message': 'Failed to process message'
                }
            )

    async def _process_streaming_message(
        self,
        session_id: str,
        user_id: str,
        content: str,
        websocket: WebSocket
    ):
        """
        Process message with streaming response.

        Sends tokens as they are generated for real-time display.

        WebSocket streaming protocol:
        1. {type: 'streamStart'} - Streaming begins
        2. {type: 'streamToken', token: '...'} - Individual token
        3. {type: 'streamEnd', messageId: '...', content: '...'} - Complete

        Args:
            session_id: UUID of the session
            user_id: UUID of the user
            content: Message content
            websocket: WebSocket connection
        """
        import uuid as uuid_module

        # Send stream start
        await manager.broadcast(
            session_id,
            {
                'type': 'streamStart',
                'senderId': 'therapist',
                'senderName': 'AI Therapist'
            }
        )

        # Token callback for streaming
        async def send_token(token: str):
            await manager.broadcast(
                session_id,
                {
                    'type': 'streamToken',
                    'token': token
                }
            )

        try:
            # Process with streaming
            response = await self.chat_service.process_private_message_stream(
                session_id,
                user_id,
                content,
                send_token
            )

            # Send stream end with final message
            await manager.broadcast(
                session_id,
                {
                    'type': 'streamEnd',
                    'messageId': response['message_id'],
                    'senderId': response['sender_id'],
                    'senderName': response['sender_name'],
                    'content': response['content'],
                    'timestamp': response['timestamp'],
                    'suggestEndSession': response.get('suggest_end_session', False)
                }
            )

        except Exception as e:
            logger.error(f"Error in streaming message: {e}", exc_info=True)
            await manager.broadcast(
                session_id,
                {
                    'type': 'streamError',
                    'message': 'Streaming failed'
                }
            )

    async def _broadcast_response(self, session_id: str, response: dict):
        """Broadcast a non-streaming response."""
        camel_response = convert_keys_to_camel(response)
        await manager.broadcast(
            session_id,
            {
                'type': 'message',
                **camel_response
            }
        )

    async def _send_sync_message(
        self,
        websocket: WebSocket,
        session_id: str
    ):
        """
        Send sync message on connection with session state.

        Args:
            websocket: WebSocket connection
            session_id: UUID of the session
        """
        from app.db.models import Session as SessionModel, Message
        import uuid

        logger.info(f"_send_sync_message: Querying session {session_id}")

        try:
            session = self.db.query(SessionModel).filter(
                SessionModel.id == uuid.UUID(session_id)
            ).first()

            if not session:
                logger.warning(f"_send_sync_message: Session {session_id} not found!")
                return

            logger.info(f"_send_sync_message: Found session, status={session.status}")

            # Get recent messages
            messages = self.db.query(Message).filter(
                Message.session_id == uuid.UUID(session_id)
            ).order_by(Message.sequence_number.desc()).limit(50).all()

            logger.info(f"_send_sync_message: Found {len(messages)} messages")

            messages.reverse()  # Chronological order

            # Send sync message with camelCase keys for frontend
            sync_message = {
                'type': 'sync',
                'sessionStatus': session.status,
                'messages': [
                    {
                        'messageId': str(m.id),
                        'senderId': m.sender_id,
                        'senderName': m.sender_name,
                        'content': m.content,
                        'timestamp': m.timestamp.isoformat(),
                        'sequenceNumber': m.sequence_number
                    }
                    for m in messages
                ]
            }
            logger.info(f"_send_sync_message: Sending sync message...")
            await manager.send_personal_message(sync_message, websocket)
            logger.info(f"_send_sync_message: Sync message sent!")
        except Exception as e:
            logger.error(f"_send_sync_message: Error - {e}", exc_info=True)
            raise
