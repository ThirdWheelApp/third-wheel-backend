"""
Chat Service

Orchestrates message processing through agents.
Handles both private and joint therapy sessions.
"""

from sqlalchemy.orm import Session
from app.db.models import Session as SessionModel, Message, LLMCall, User, CheckIn
from app.agents.private_agent.agent import PrivateAgent
from app.agents.private_agent.repo import PrivateAgentRepository
from app.agents.joint_agent.agent import JointAgent
from app.agents.joint_agent.repo import JointAgentRepository
from app.services.therapist_notes_service import TherapistNotesService
from app.services.privacy_boundary_service import PrivacyBoundaryService
from app.utils.logger import get_logger
from typing import Dict, Optional, AsyncIterator, AsyncGenerator, Callable, Awaitable, List
import uuid
from datetime import datetime
from datetime import date, timedelta
import re

logger = get_logger(__name__)


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
        self.notes_service = TherapistNotesService(db)

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
        self._ensure_user_can_chat(session, user_id, expected_type="private")

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
        # Handle None group_id for solo private sessions (no partner yet)
        repo = PrivateAgentRepository(self.db)
        group_id = str(session.group_id) if session.group_id else None
        agent = PrivateAgent(user_id, group_id, repo)

        note_context = self.notes_service.get_recent_note_context(
            session_id=session_id,
            scope="private",
            limit=4
        )
        prompt_content = content
        if note_context:
            prompt_content = (
                f"{content}\n\n"
                f"[Internal therapist continuity notes - do not mention these explicitly]\n"
                f"{note_context}"
            )

        # Generate response
        response_text = await agent.get_private_message(prompt_content, messages_history)

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

        try:
            self.notes_service.create_turn_note(
                session_id=session_id,
                scope="private",
                turn_sequence=therapist_message.sequence_number,
                user_id=user_id,
                user_message=content,
                therapist_message=response_text,
                task_proposals=[]
            )
        except Exception as e:
            logger.warning(f"Failed to persist private therapist turn note: {e}")

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
        self._ensure_user_can_chat(session, user_id, expected_type="private")

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
        # Handle None group_id for solo private sessions (no partner yet)
        repo = PrivateAgentRepository(self.db)
        group_id = str(session.group_id) if session.group_id else None
        agent = PrivateAgent(user_id, group_id, repo)

        note_context = self.notes_service.get_recent_note_context(
            session_id=session_id,
            scope="private",
            limit=4
        )
        prompt_content = content
        if note_context:
            prompt_content = (
                f"{content}\n\n"
                f"[Internal therapist continuity notes - do not mention these explicitly]\n"
                f"{note_context}"
            )

        # Stream response and collect full text
        full_response = ""
        async for token in agent.get_private_message_stream(prompt_content, messages_history):
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

        try:
            self.notes_service.create_turn_note(
                session_id=session_id,
                scope="private",
                turn_sequence=therapist_message.sequence_number,
                user_id=user_id,
                user_message=content,
                therapist_message=full_response,
                task_proposals=[]
            )
        except Exception as e:
            logger.warning(f"Failed to persist private streaming therapist turn note: {e}")

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
        self._ensure_user_can_chat(session, user_id, expected_type="joint")

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

        note_context = self.notes_service.get_recent_note_context(
            session_id=session_id,
            scope="joint",
            limit=4
        )
        prompt_content = content
        if note_context:
            prompt_content = (
                f"{content}\n\n"
                f"[Internal therapist continuity notes - do not mention these explicitly]\n"
                f"{note_context}"
            )

        # Generate response
        response_text, suggest_end, updated_context = await joint_agent.process_message(
            prompt_content,
            user.name,
            messages_history,
            accumulated_context
        )
        privacy_check = PrivacyBoundaryService.validate_joint_response(response_text)
        if not privacy_check.ok:
            logger.warning(
                f"Joint response failed privacy validation ({privacy_check.reasons}); replacing response."
            )
            response_text = (
                "Let's slow down and stay with what each of you is ready to say here together. "
                "What feels most important to name about trust, distance, or repair right now?"
            )
            suggest_end = False

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

        task_proposals = self._create_task_proposals_from_message(
            session=session,
            sender_id=user_id,
            content=content
        )

        # Log LLM call
        metrics = joint_agent.get_last_metrics()
        if metrics:
            self._log_llm_call(session_id, metrics)

        self.db.commit()

        try:
            self.notes_service.create_turn_note(
                session_id=session_id,
                scope="joint",
                turn_sequence=therapist_message.sequence_number,
                user_id=user_id,
                user_message=content,
                therapist_message=response_text,
                task_proposals=task_proposals
            )
        except Exception as e:
            logger.warning(f"Failed to persist joint therapist turn note: {e}")

        return {
            'message_id': str(therapist_message.id),
            'sender_id': 'therapist',
            'sender_name': 'AI Therapist',
            'content': response_text,
            'timestamp': therapist_message.timestamp.isoformat(),
            'suggest_end_session': suggest_end,
            'task_proposals': task_proposals
        }

    def _get_next_sequence_number(self, session_id: str) -> int:
        """Get next sequence number for message in session."""
        max_seq = self.db.query(Message.sequence_number).filter(
            Message.session_id == uuid.UUID(session_id)
        ).order_by(Message.sequence_number.desc()).first()

        return (max_seq[0] + 1) if max_seq else 1

    def _ensure_user_can_chat(
        self,
        session: SessionModel,
        user_id: str,
        expected_type: Optional[str] = None
    ) -> None:
        """Validate that the user can send messages in this session."""
        user_uuid = uuid.UUID(user_id)
        if user_uuid not in session.participants:
            raise PermissionError("Not authorized to access this session")
        if expected_type and session.type != expected_type:
            raise ValueError(f"Session {session.id} is not a {expected_type} session")
        if session.type == "joint" and not session.group_id:
            raise ValueError("Joint sessions require a group_id")

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

    def _create_task_proposals_from_message(
        self,
        session: SessionModel,
        sender_id: str,
        content: str
    ) -> List[Dict]:
        """
        Detect simple accountability-task intent and create proposed tasks.

        POC behavior:
        - Only for joint sessions with at least 2 participants
        - Rule-based parsing for high-confidence proposal candidates
        """
        if session.type != "joint" or len(session.participants) < 2 or not session.group_id:
            return []

        parsed = self._parse_task_intent(session, sender_id, content)
        if not parsed:
            return []

        assignee_id = parsed["assignee_id"]
        verifier_id = parsed["verifier_id"]

        checkin = CheckIn(
            id=uuid.uuid4(),
            group_id=session.group_id,
            assigned_to=uuid.UUID(assignee_id),
            verifier_id=uuid.UUID(verifier_id) if verifier_id else None,
            title=parsed["title"],
            description=parsed["description"],
            status="proposed",
            assigned_approved=False,
            verifier_approved=True,
            requires_verification=bool(verifier_id),
            frequency=parsed["frequency"],
            progress={"completed": 0, "total": parsed["duration_days"]},
            next_check_date=date.today() + timedelta(days=1),
            created_from_session=session.id,
            created_at=datetime.utcnow()
        )

        self.db.add(checkin)
        self.db.commit()
        self.db.refresh(checkin)

        return [{
            "id": str(checkin.id),
            "group_id": str(checkin.group_id),
            "title": checkin.title,
            "description": checkin.description,
            "status": checkin.status,
            "assigned_to": str(checkin.assigned_to),
            "verifier_id": str(checkin.verifier_id) if checkin.verifier_id else None,
            "frequency": checkin.frequency,
            "duration_days": (checkin.progress or {}).get("total", 7),
            "created_at": checkin.created_at.isoformat()
        }]

    def _parse_task_intent(
        self,
        session: SessionModel,
        sender_id: str,
        content: str
    ) -> Optional[Dict]:
        text = (content or "").strip()
        lower = text.lower()

        # Require reasonably strong signal to avoid noisy proposals.
        trigger = (
            "task" in lower or
            "check-in" in lower or
            "check in" in lower or
            "accountability" in lower or
            "should " in lower or
            "needs to" in lower or
            "need to" in lower
        )
        if not trigger:
            return None

        participants = [str(p) for p in session.participants]
        sender_uuid = sender_id
        partner_uuid = next((p for p in participants if p != sender_uuid), None)
        if not partner_uuid:
            return None

        sender = self.db.query(User).filter(User.id == uuid.UUID(sender_uuid)).first()
        partner = self.db.query(User).filter(User.id == uuid.UUID(partner_uuid)).first()
        sender_name = (sender.name or "").split(" ")[0].lower() if sender else ""
        partner_name = (partner.name or "").split(" ")[0].lower() if partner else ""

        assignee_id = partner_uuid
        if " i will " in f" {lower} " or " i'll " in f" {lower} ":
            assignee_id = sender_uuid
        if sender_name and sender_name in lower and ("should" in lower or "needs to" in lower):
            assignee_id = sender_uuid
        if partner_name and partner_name in lower and ("should" in lower or "needs to" in lower):
            assignee_id = partner_uuid

        verifier_id = sender_uuid if assignee_id == partner_uuid else partner_uuid

        frequency = "daily" if ("daily" in lower or "every day" in lower) else "weekly" if "weekly" in lower else "daily"

        duration_days = 7
        match_days = re.search(r"(\d+)\s*day", lower)
        if match_days:
            duration_days = max(1, min(90, int(match_days.group(1))))

        # If a "task:" prefix exists, use the remaining string as the title seed.
        title_seed = text
        if "task:" in lower:
            split_idx = lower.index("task:") + len("task:")
            title_seed = text[split_idx:].strip()
        if len(title_seed) > 90:
            title_seed = title_seed[:90].rstrip() + "..."

        title = title_seed or "Accountability task"
        if not title.lower().startswith("task"):
            title = f"Task: {title}"

        return {
            "assignee_id": assignee_id,
            "verifier_id": verifier_id,
            "title": title,
            "description": text,
            "frequency": frequency,
            "duration_days": duration_days
        }
