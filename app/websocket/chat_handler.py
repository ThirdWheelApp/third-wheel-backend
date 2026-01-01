"""
Chat WebSocket Handler

Handles WebSocket messages for chat sessions.
Coordinates with ChatService and broadcasts responses.
"""

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from app.websocket.manager import manager
from app.services.chat_service import ChatService
from app.utils.message_queue import session_message_queue
import json


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
        # Connect
        await manager.connect(session_id, user_id, websocket)

        try:
            # Send sync message with current session state
            await self._send_sync_message(websocket, session_id)

            # Listen for messages
            while True:
                data = await websocket.receive_json()
                await self._handle_message(
                    data,
                    session_id,
                    user_id,
                    websocket
                )

        except WebSocketDisconnect:
            manager.disconnect(session_id, websocket)
        except Exception as e:
            print(f"Error in WebSocket handler: {e}")
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

        Args:
            data: Message data
            session_id: UUID of the session
            user_id: UUID of the user
            websocket: WebSocket connection
        """
        content = data.get('content', '')

        if not content.strip():
            return

        try:
            # Broadcast that AI is typing
            await manager.broadcast(
                session_id,
                {
                    'type': 'typing',
                    'user_id': 'therapist',
                    'is_typing': True
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

            # Process through appropriate service
            if session.type == 'private':
                response = await self.chat_service.process_private_message(
                    session_id,
                    user_id,
                    content
                )
            else:  # joint
                response = await self.chat_service.process_joint_message(
                    session_id,
                    user_id,
                    content
                )

            # Stop typing indicator
            await manager.broadcast(
                session_id,
                {
                    'type': 'typing',
                    'user_id': 'therapist',
                    'is_typing': False
                }
            )

            # Broadcast response
            await manager.broadcast(
                session_id,
                {
                    'type': 'message',
                    **response
                }
            )

        except Exception as e:
            print(f"Error processing message: {e}")
            await manager.broadcast(
                session_id,
                {
                    'type': 'error',
                    'message': 'Failed to process message'
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

        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            return

        # Get recent messages
        messages = self.db.query(Message).filter(
            Message.session_id == uuid.UUID(session_id)
        ).order_by(Message.sequence_number.desc()).limit(50).all()

        messages.reverse()  # Chronological order

        await manager.send_personal_message(
            {
                'type': 'sync',
                'session_status': session.status,
                'messages': [
                    {
                        'message_id': str(m.id),
                        'sender_id': m.sender_id,
                        'sender_name': m.sender_name,
                        'content': m.content,
                        'timestamp': m.timestamp.isoformat(),
                        'sequence_number': m.sequence_number
                    }
                    for m in messages
                ]
            },
            websocket
        )
