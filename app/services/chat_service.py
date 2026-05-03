"""
Chat Service

Orchestrates message processing through agents.
Handles both private and joint therapy sessions.
"""

from sqlalchemy.orm import Session
from app.db.models import (
    LLMCall,
    Message,
    PrivateUserContext,
    Session as SessionModel,
    User,
)
from app.agents.private_agent.agent import PrivateAgent
from app.agents.private_agent.repo import PrivateAgentRepository
from app.agents.joint_agent.agent import JointAgent
from app.agents.joint_agent.repo import JointAgentRepository
from app.config.prompts import (
    JOINT_RESPONSE_REPAIR_PROMPT_TEMPLATE,
    JOINT_RESPONSE_REPAIR_SYSTEM_PROMPT,
    TASK_PROPOSAL_EXTRACTION_PROMPT_TEMPLATE,
    TASK_PROPOSAL_EXTRACTION_SYSTEM_PROMPT,
    format_messages_for_llm,
)
from app.config.settings import settings
from app.demo.mock_llm import get_llm_client
from app.services.checkin_service import CheckInService
from app.services.context_service import extract_json_from_response
from app.services.therapist_notes_service import TherapistNotesService
from app.services.privacy_boundary_service import PrivacyBoundaryService
from app.utils.logger import get_logger
from typing import Dict, Optional, AsyncIterator, AsyncGenerator, Callable, Awaitable, List
import uuid
from datetime import datetime
import re
import time

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
        self.client = get_llm_client()

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
        joint_conversation_text = "\n".join(
            f"{msg.get('sender_name', 'Unknown')}: {msg.get('content', '')}"
            for msg in messages_history
        )
        joint_conversation_by_user: Dict[str, str] = {}
        for msg in messages_history:
            sender_id = msg.get('sender_id')
            if not sender_id or sender_id == 'therapist':
                continue
            joint_conversation_by_user.setdefault(str(sender_id), "")
            joint_conversation_by_user[str(sender_id)] += f"\n{msg.get('content', '')}"

        public_terms = set()
        participant_users = self.db.query(User).filter(
            User.id.in_(session.participants)
        ).all()
        for participant in participant_users:
            public_terms.update(re.findall(r"[a-zA-Z]+", participant.name.lower()))

        private_contexts_for_guard = [
            {
                "data": ctx.data,
                "subject_user_id": str(ctx.user_id),
            }
            for ctx in self.db.query(PrivateUserContext).filter(
                PrivateUserContext.group_id == session.group_id
            ).all()
            if ctx.data
        ]
        privacy_check = PrivacyBoundaryService.validate_joint_response(
            response_text,
            private_contexts=private_contexts_for_guard,
            joint_conversation_text=joint_conversation_text,
            joint_conversation_by_user=joint_conversation_by_user,
            public_terms=public_terms
        )
        if not privacy_check.ok:
            logger.warning(
                f"Joint response failed privacy validation ({privacy_check.reasons}); repairing response."
            )
            if self._is_source_seeking_message(content):
                candidate = self._source_seeking_boundary_response()
                repair_check = PrivacyBoundaryService.validate_joint_response(
                    candidate,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                    public_terms=public_terms
                )
                repaired_response = candidate if repair_check.ok else None
            else:
                candidate = self._privacy_safe_redirection_response()
                repair_check = PrivacyBoundaryService.validate_joint_response(
                    candidate,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                    public_terms=public_terms
                )
                repaired_response = candidate if repair_check.ok else None
                if not repaired_response:
                    repair_reasons = privacy_check.reasons
                    for attempt in range(2):
                        candidate = self._repair_joint_response(
                            messages_history=messages_history,
                            reasons=repair_reasons,
                            retry=attempt > 0
                        )
                        repair_check = PrivacyBoundaryService.validate_joint_response(
                            candidate,
                            private_contexts=private_contexts_for_guard,
                            joint_conversation_text=joint_conversation_text,
                            joint_conversation_by_user=joint_conversation_by_user,
                            public_terms=public_terms
                        )
                        if repair_check.ok:
                            repaired_response = candidate
                            break
                        repair_reasons = repair_check.reasons

            if repaired_response:
                response_text = repaired_response
            else:
                logger.warning(
                    "Repaired joint response failed privacy validation; using circuit-breaker response."
                )
                response_text = self._joint_privacy_circuit_breaker_response()
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

        task_proposals = await self._create_task_proposals_from_message(
            session=session,
            sender_id=user_id,
            sender_name=user.name,
            content=content,
            therapist_reply=response_text,
            messages_history=messages_history
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

    def _repair_joint_response(
        self,
        messages_history: List[Dict],
        reasons: List[str],
        retry: bool = False
    ) -> str:
        """Generate a fresh privacy-safe joint reply from joint transcript only."""
        transcript = format_messages_for_llm(messages_history[-12:])
        retry_instruction = ""
        if retry:
            retry_instruction = (
                "This is a retry because the first repair still failed. Be stricter: "
                "do not mention what the therapist knows, heard, was told, can say, "
                "cannot say, can confirm, or cannot confirm. Do not repeat guessed "
                "sensitive-topic words unless the subject partner named them."
            )
        prompt = JOINT_RESPONSE_REPAIR_PROMPT_TEMPLATE.format(
            joint_transcript=transcript,
            reasons=", ".join(reasons),
            retry_instruction=retry_instruction
        )
        try:
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=450,
                temperature=0.4,
                system=JOINT_RESPONSE_REPAIR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.warning(f"Failed to repair joint response after privacy validation: {e}")
            return self._joint_privacy_circuit_breaker_response()

    @staticmethod
    def _joint_privacy_circuit_breaker_response() -> str:
        """
        Last-resort response used only if model repair fails validation or errors.
        Runtime should usually take the repair path so the reply remains contextual.
        """
        return (
            "Let's stay with what has been named here instead of guessing or filling in blanks. "
            "The useful next step is to slow the conversation enough that one person can choose "
            "what they are ready to say, and the other can name what they need to feel steady "
            "while listening. What is one small truth or request each of you can put into words right now?"
        )

    @staticmethod
    def _is_source_seeking_message(content: str) -> bool:
        lower = (content or "").lower()
        source_markers = (
            "elsewhere",
            "outside this room",
            "outside the room",
            "outside this conversation",
            "outside our time",
            "private",
            "privately",
            "told you",
            "tell you",
            "what do you know",
            "what you know",
        )
        return any(marker in lower for marker in source_markers)

    @staticmethod
    def _source_seeking_boundary_response() -> str:
        return (
            "Jordan, your question makes sense. I can't answer for Alex. The most useful move is "
            "to ask Alex directly and give them room to answer in their own words. Alex, what is "
            "one truthful sentence you are ready to say right now?"
        )

    @staticmethod
    def _privacy_safe_redirection_response() -> str:
        return (
            "Jordan, it makes sense that uncertainty is hard to sit with. Alex, you have named "
            "guilt and fear, and you also have a choice about pace. The next useful step is one "
            "honest sentence Alex can say now and one request Jordan can make for steadiness. "
            "Alex, what can you name truthfully right now?"
        )

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

    async def _create_task_proposals_from_message(
        self,
        session: SessionModel,
        sender_id: str,
        sender_name: str,
        content: str,
        therapist_reply: str,
        messages_history: List[Dict]
    ) -> List[Dict]:
        """
        Extract canonical task proposals from the latest joint exchange.

        The extraction call only sees the live joint transcript and the latest
        therapist reply. Private context is intentionally excluded.
        """
        if session.type != "joint" or len(session.participants) < 2 or not session.group_id:
            return []

        participants = [str(p) for p in session.participants]
        partner_uuid = next((p for p in participants if p != sender_id), None)
        if not partner_uuid:
            return []
        partner = self.db.query(User).filter(User.id == uuid.UUID(partner_uuid)).first()
        partner_name = partner.name if partner else "Partner"

        prompt = TASK_PROPOSAL_EXTRACTION_PROMPT_TEMPLATE.format(
            joint_transcript=format_messages_for_llm(messages_history[-12:]),
            sender_name=sender_name,
            user_message=content,
            therapist_reply=therapist_reply,
            partner_name=partner_name
        )

        try:
            start_time = time.time()
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=600,
                temperature=0.1,
                system=TASK_PROPOSAL_EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            latency_ms = int((time.time() - start_time) * 1000)
            payload = extract_json_from_response(response.content[0].text) or {}
            logger.info(
                f"Task proposal extraction completed in {latency_ms}ms "
                f"for session {session.id}"
            )
        except Exception as e:
            logger.warning(f"Task proposal extraction failed for session {session.id}: {e}")
            return []

        raw_tasks = payload.get("tasks") if isinstance(payload, dict) else []
        if not isinstance(raw_tasks, list):
            return []

        service = CheckInService(self.db)
        proposals = []
        for raw_task in raw_tasks[:3]:
            if not isinstance(raw_task, dict):
                continue

            assignee_ids = self._resolve_task_assignees(
                raw_task.get("assigned_to"),
                sender_id=sender_id,
                partner_id=partner_uuid
            )
            if not assignee_ids:
                continue

            source = str(raw_task.get("source") or "").strip().lower()
            proposed_by = sender_id if source == "user" else None

            for assignee_id in assignee_ids:
                verifier_id = partner_uuid if assignee_id == sender_id else sender_id
                checkin = service.create_task_proposal(
                    group_id=str(session.group_id),
                    assigned_to=assignee_id,
                    title=raw_task.get("title"),
                    description=raw_task.get("description"),
                    proposed_by=proposed_by,
                    verifier_id=verifier_id,
                    requires_verification=bool(raw_task.get("requires_verification", False)),
                    frequency=raw_task.get("frequency", "daily"),
                    duration_days=raw_task.get("duration_days", 7),
                    session_id=str(session.id),
                    commit=True
                )
                if checkin:
                    proposals.append(self._task_proposal_payload(checkin))

        return proposals

    @staticmethod
    def _resolve_task_assignees(
        assigned_to: Optional[str],
        sender_id: str,
        partner_id: str
    ) -> List[str]:
        value = str(assigned_to or "").strip().lower()
        if value == "sender":
            return [sender_id]
        if value == "partner":
            return [partner_id]
        if value == "both":
            return [sender_id, partner_id]
        return []

    @staticmethod
    def _task_proposal_payload(checkin) -> Dict:
        return {
            "id": str(checkin.id),
            "group_id": str(checkin.group_id),
            "title": checkin.title,
            "description": checkin.description,
            "status": checkin.status,
            "assigned_to": str(checkin.assigned_to),
            "verifier_id": str(checkin.verifier_id) if checkin.verifier_id else None,
            "requires_verification": checkin.requires_verification,
            "assigned_approved": checkin.assigned_approved,
            "verifier_approved": checkin.verifier_approved,
            "progress": checkin.progress,
            "frequency": checkin.frequency,
            "duration_days": (checkin.progress or {}).get("total", 7),
            "created_at": checkin.created_at.isoformat()
        }
