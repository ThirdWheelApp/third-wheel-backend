"""
Chat Service

Orchestrates message processing through agents.
Handles both private and joint therapy sessions.
"""

from sqlalchemy.orm import Session
from app.db.models import Session as SessionModel, Message, LLMCall, User
from app.agents.private_agent.agent import PrivateAgent
from app.agents.private_agent.repo import PrivateAgentRepository
from app.agents.joint_agent.agent import JointAgent
from app.agents.joint_agent.repo import JointAgentRepository
from typing import Dict, Optional, AsyncIterator, AsyncGenerator, Callable, Awaitable
import uuid
from datetime import datetime


class ChatService:
    """
    Service for processing chat messages through AI agents.

    Responsibilities:
    - Route messages to appropriate agent (private vs joint)
    - Save messages to database
    - Log LLM calls for monitoring
    - Manage session state
    """

    def __init__(self, db: Session):
        self.db = db

    async def process_private_message(
        self,
        session_id: str,
        user_id: str,
        content: str
    ) -> Dict:
        """
        Process a message in a private therapy session.

        Args:
            session_id: UUID of the session
            user_id: UUID of the user sending the message
            content: Message content

        Returns:
            Dictionary with response and metadata
        """
        # Get session
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Get user
        user = self.db.query(User).filter(User.id == uuid.UUID(user_id)).first()
        if not user:
            raise ValueError(f"User {user_id} not found")

        # Save user message
        user_message = Message(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            sender_id=user_id,
            sender_name=user.name,
            content=content,
            sequence_number=self._get_next_sequence_number(session_id),
            timestamp=datetime.utcnow()
        )
        self.db.add(user_message)
        self.db.commit()

        # Get message history
        messages = self.db.query(Message).filter(
            Message.session_id == uuid.UUID(session_id)
        ).order_by(Message.sequence_number).all()

        messages_history = [
            {
                'sender_id': str(m.sender_id),
                'sender_name': m.sender_name,
                'content': m.content,
                'timestamp': m.timestamp.isoformat()
            }
            for m in messages
        ]

        # Create Private Agent
        repo = PrivateAgentRepository(self.db)
        agent = PrivateAgent(user_id, str(session.group_id), repo)

        # Generate response
        response_text = await agent.get_private_message(content, messages_history)

        # Save therapist message
        therapist_message = Message(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            sender_id='therapist',
            sender_name='AI Therapist',
            content=response_text,
            sequence_number=self._get_next_sequence_number(session_id),
            timestamp=datetime.utcnow()
        )
        self.db.add(therapist_message)

        # Log LLM call
        metrics = agent.get_last_metrics()
        if metrics:
            self._log_llm_call(session_id, metrics)

        self.db.commit()

        return {
            'message_id': str(therapist_message.id),
            'sender_id': 'therapist',
            'sender_name': 'AI Therapist',
            'content': response_text,
            'timestamp': therapist_message.timestamp.isoformat(),
            'suggest_end_session': False
        }

    async def process_private_message_stream(
        self,
        session_id: str,
        user_id: str,
        content: str,
        on_token: Callable[[str], Awaitable[None]]
    ) -> Dict:
        """
        Process a private message with streaming response.

        Args:
            session_id: UUID of the session
            user_id: UUID of the user
            content: Message content
            on_token: Async callback for each token

        Returns:
            Dictionary with final message metadata
        """
        # Get session
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Get user
        user = self.db.query(User).filter(User.id == uuid.UUID(user_id)).first()
        if not user:
            raise ValueError(f"User {user_id} not found")

        # Save user message
        user_message = Message(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            sender_id=user_id,
            sender_name=user.name,
            content=content,
            sequence_number=self._get_next_sequence_number(session_id),
            timestamp=datetime.utcnow()
        )
        self.db.add(user_message)
        self.db.commit()

        # Get message history
        messages = self.db.query(Message).filter(
            Message.session_id == uuid.UUID(session_id)
        ).order_by(Message.sequence_number).all()

        messages_history = [
            {
                'sender_id': str(m.sender_id),
                'sender_name': m.sender_name,
                'content': m.content,
                'timestamp': m.timestamp.isoformat()
            }
            for m in messages
        ]

        # Create Private Agent
        repo = PrivateAgentRepository(self.db)
        agent = PrivateAgent(user_id, str(session.group_id), repo)

        # Stream response and collect full text
        full_response = ""
        async for token in agent.get_private_message_stream(content, messages_history):
            full_response += token
            await on_token(token)

        # Save therapist message
        therapist_message = Message(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            sender_id='therapist',
            sender_name='AI Therapist',
            content=full_response,
            sequence_number=self._get_next_sequence_number(session_id),
            timestamp=datetime.utcnow()
        )
        self.db.add(therapist_message)

        # Log LLM call
        metrics = agent.get_last_metrics()
        if metrics:
            self._log_llm_call(session_id, metrics)

        self.db.commit()

        return {
            'message_id': str(therapist_message.id),
            'sender_id': 'therapist',
            'sender_name': 'AI Therapist',
            'content': full_response,
            'timestamp': therapist_message.timestamp.isoformat(),
            'suggest_end_session': False
        }

    async def process_joint_message(
        self,
        session_id: str,
        user_id: str,
        content: str
    ) -> Dict:
        """
        Process a message in a joint therapy session.

        Args:
            session_id: UUID of the session
            user_id: UUID of the user sending the message
            content: Message content

        Returns:
            Dictionary with response and metadata
        """
        # Get session
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Get user
        user = self.db.query(User).filter(User.id == uuid.UUID(user_id)).first()
        if not user:
            raise ValueError(f"User {user_id} not found")

        # Save user message
        user_message = Message(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            sender_id=user_id,
            sender_name=user.name,
            content=content,
            sequence_number=self._get_next_sequence_number(session_id),
            timestamp=datetime.utcnow()
        )
        self.db.add(user_message)
        self.db.commit()

        # Get message history
        messages = self.db.query(Message).filter(
            Message.session_id == uuid.UUID(session_id)
        ).order_by(Message.sequence_number).all()

        messages_history = [
            {
                'sender_id': str(m.sender_id),
                'sender_name': m.sender_name,
                'content': m.content,
                'timestamp': m.timestamp.isoformat()
            }
            for m in messages
        ]

        # Create Private Agents for both partners
        private_agent_a = None
        private_agent_b = None

        if len(session.participants) >= 2:
            repo_a = PrivateAgentRepository(self.db)
            private_agent_a = PrivateAgent(
                str(session.participants[0]),
                str(session.group_id),
                repo_a
            )

            repo_b = PrivateAgentRepository(self.db)
            private_agent_b = PrivateAgent(
                str(session.participants[1]),
                str(session.group_id),
                repo_b
            )

        # Create Joint Agent
        joint_repo = JointAgentRepository(self.db)
        joint_agent = JointAgent(
            str(session.group_id),
            joint_repo,
            private_agent_a,
            private_agent_b
        )

        # Get accumulated context from session
        accumulated_context = session.current_context or {}

        # Generate response
        response_text, suggest_end, updated_context = await joint_agent.process_message(
            content,
            user.name,
            messages_history,
            accumulated_context
        )

        # Update session context
        session.current_context = updated_context
        self.db.commit()

        # Save therapist message
        therapist_message = Message(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            sender_id='therapist',
            sender_name='AI Therapist',
            content=response_text,
            sequence_number=self._get_next_sequence_number(session_id),
            message_metadata={'suggest_end_session': suggest_end} if suggest_end else None,
            timestamp=datetime.utcnow()
        )
        self.db.add(therapist_message)

        # Log LLM call
        metrics = joint_agent.get_last_metrics()
        if metrics:
            self._log_llm_call(session_id, metrics)

        self.db.commit()

        return {
            'message_id': str(therapist_message.id),
            'sender_id': 'therapist',
            'sender_name': 'AI Therapist',
            'content': response_text,
            'timestamp': therapist_message.timestamp.isoformat(),
            'suggest_end_session': suggest_end
        }

    def _get_next_sequence_number(self, session_id: str) -> int:
        """Get next sequence number for message in session."""
        max_seq = self.db.query(Message.sequence_number).filter(
            Message.session_id == uuid.UUID(session_id)
        ).order_by(Message.sequence_number.desc()).first()

        return (max_seq[0] + 1) if max_seq else 1

    def _log_llm_call(self, session_id: str, metrics: Dict):
        """Log LLM call for monitoring and cost tracking."""
        llm_call = LLMCall(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            agent_type=metrics.get('agent_type'),
            model=metrics.get('model'),
            input_tokens=metrics.get('input_tokens'),
            output_tokens=metrics.get('output_tokens'),
            latency_ms=metrics.get('latency_ms'),
            created_at=datetime.utcnow()
        )
        self.db.add(llm_call)
