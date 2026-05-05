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
    JOINT_PRIVACY_ARBITER_PROMPT_TEMPLATE,
    JOINT_PRIVACY_ARBITER_SYSTEM_PROMPT,
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
from app.services.privacy_boundary_service import PrivacyBoundaryService, PrivacyValidationResult
from app.utils.logger import get_logger
from typing import Dict, Optional, AsyncIterator, AsyncGenerator, Callable, Awaitable, List
import uuid
from datetime import datetime
import json
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
            if ctx.data and self._should_guard_private_context(ctx.data)
        ]
        privacy_check = PrivacyBoundaryService.validate_joint_response(
            response_text,
            private_contexts=private_contexts_for_guard,
            joint_conversation_text=joint_conversation_text,
            joint_conversation_by_user=joint_conversation_by_user,
            public_terms=public_terms
        )
        if self._semantic_privacy_arbiter_allows(
            response_text=response_text,
            privacy_reasons=privacy_check.reasons,
            private_contexts=private_contexts_for_guard,
            joint_conversation_text=joint_conversation_text,
            joint_conversation_by_user=joint_conversation_by_user,
        ):
            privacy_check = PrivacyValidationResult(ok=True, reasons=[])

        if not privacy_check.ok:
            logger.warning(
                f"Joint response failed privacy validation ({privacy_check.reasons}); repairing response."
            )
            repaired_response = None
            source_seeking = self._is_source_seeking_message(content)
            repair_instruction = ""
            if source_seeking:
                repair_instruction = (
                    "The latest message asks about what the therapist knows or about information "
                    "from another setting. Do not answer the source question. Write a specific "
                    "couples-therapy reply that helps the speaker ask their partner directly, "
                    "without mentioning information sources, confidentiality, or the therapist as "
                    "the wrong person to ask."
                )

            repair_reasons = privacy_check.reasons
            for attempt in range(2):
                candidate = self._repair_joint_response(
                    messages_history=messages_history,
                    reasons=repair_reasons,
                    retry=attempt > 0,
                    extra_instruction=repair_instruction
                )
                repair_check = PrivacyBoundaryService.validate_joint_response(
                    candidate,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                    public_terms=public_terms
                )
                if self._semantic_privacy_arbiter_allows(
                    response_text=candidate,
                    privacy_reasons=repair_check.reasons,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                ):
                    repair_check = PrivacyValidationResult(ok=True, reasons=[])
                if self._is_circuit_breaker_response(candidate) or self._is_low_signal_joint_response(candidate):
                    repair_reasons = repair_check.reasons or ["repair_returned_low_signal_response"]
                    continue
                if repair_check.ok:
                    repaired_response = candidate
                    break
                repair_reasons = repair_check.reasons

            if self._is_source_seeking_message(content):
                candidate = self._source_seeking_boundary_response(messages_history)
                repair_check = PrivacyBoundaryService.validate_joint_response(
                    candidate,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                    public_terms=public_terms
                )
                if not repaired_response and repair_check.ok:
                    repaired_response = candidate

            if not repaired_response:
                candidate = self._privacy_safe_redirection_response(messages_history)
                repair_check = PrivacyBoundaryService.validate_joint_response(
                    candidate,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                    public_terms=public_terms
                )
                repaired_response = candidate if repair_check.ok else None

            if repaired_response:
                response_text = repaired_response
            else:
                logger.warning(
                    "Repaired joint response failed privacy validation; using grounded fallback response."
                )
                response_text = self._privacy_safe_redirection_response(messages_history)
            suggest_end = False

        opened_terms = PrivacyBoundaryService.opened_sensitive_terms_for_subject(
            content,
            private_contexts_for_guard,
            user_id,
        )
        if opened_terms and not self._contains_any_term(response_text, opened_terms):
            logger.warning(
                "Joint response missed explicit live disclosure acknowledgement; repairing response."
            )
            repaired_response = None
            repair_instruction = (
                "The latest speaker has made these exact words public in the live transcript: "
                f"{', '.join(opened_terms[:4])}. "
                "The fresh reply must acknowledge at least one of those exact words or phrases, "
                "and must not add any details beyond the live transcript."
            )
            repair_reasons = ["missing_live_disclosure_acknowledgement"]
            for attempt in range(2):
                candidate = self._repair_joint_response(
                    messages_history=messages_history,
                    reasons=repair_reasons,
                    retry=attempt > 0,
                    extra_instruction=repair_instruction,
                )
                repair_check = PrivacyBoundaryService.validate_joint_response(
                    candidate,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                    public_terms=public_terms
                )
                if repair_check.ok and self._contains_any_term(candidate, opened_terms):
                    repaired_response = candidate
                    break
                repair_reasons = repair_check.reasons or repair_reasons

            if repaired_response:
                response_text = repaired_response
                suggest_end = False
            else:
                candidate = self._prepend_live_disclosure_acknowledgement(
                    response_text,
                    opened_terms[0],
                )
                candidate_check = PrivacyBoundaryService.validate_joint_response(
                    candidate,
                    private_contexts=private_contexts_for_guard,
                    joint_conversation_text=joint_conversation_text,
                    joint_conversation_by_user=joint_conversation_by_user,
                    public_terms=public_terms
                )
                if candidate_check.ok:
                    response_text = candidate
                    suggest_end = False

        if self._is_low_signal_joint_response(response_text):
            logger.warning("Joint response was privacy-safe but too generic; repairing for specificity.")
            specificity_instruction = (
                "The previous draft was too generic. Write a fresh reply that is specific to the "
                "latest live message. Do not use boundary filler, do not use the phrase 'one small truth', "
                "and do not ask both partners to generically name what they are ready to say. Give one "
                "concrete conversational move tied to the latest speaker's words."
            )
            candidate = self._repair_joint_response(
                messages_history=messages_history,
                reasons=["response_too_generic"],
                extra_instruction=specificity_instruction,
            )
            candidate_check = PrivacyBoundaryService.validate_joint_response(
                candidate,
                private_contexts=private_contexts_for_guard,
                joint_conversation_text=joint_conversation_text,
                joint_conversation_by_user=joint_conversation_by_user,
                public_terms=public_terms
            )
            if (
                candidate_check.ok
                and not self._is_circuit_breaker_response(candidate)
                and not self._is_low_signal_joint_response(candidate)
            ):
                response_text = candidate
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

    @staticmethod
    def _privacy_failure_is_source_specific_only(reasons: List[str]) -> bool:
        return bool(reasons) and set(reasons) == {"response_mentions_private_source_specifics"}

    def _semantic_privacy_arbiter_allows(
        self,
        response_text: str,
        privacy_reasons: List[str],
        private_contexts: List[Dict],
        joint_conversation_text: str,
        joint_conversation_by_user: Dict[str, str],
    ) -> bool:
        """
        Resolve lexical source-specific false positives with a semantic judge.

        The lexical guard is intentionally conservative. When its only concern
        is source-specific overlap, a small LLM arbiter can distinguish broad
        therapy language from actual private fact leakage.
        """
        if not self._privacy_failure_is_source_specific_only(privacy_reasons):
            return False
        if not response_text or not private_contexts:
            return False

        private_payload = []
        for context in private_contexts[:20]:
            data = context.get("data") if isinstance(context.get("data"), dict) else context
            private_payload.append({
                "subject_user_id": context.get("subject_user_id"),
                "text": (data or {}).get("text", ""),
                "tags": (data or {}).get("tags", []),
                "category": (data or {}).get("category", ""),
            })

        prompt = JOINT_PRIVACY_ARBITER_PROMPT_TEMPLATE.format(
            private_contexts=json.dumps(private_payload, indent=2),
            joint_transcript=joint_conversation_text,
            joint_transcript_by_user=json.dumps(joint_conversation_by_user, indent=2),
            response_text=response_text,
        )

        try:
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=200,
                temperature=0,
                system=JOINT_PRIVACY_ARBITER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            payload = extract_json_from_response(response.content[0].text) or {}
            allowed = payload.get("safe") is True
            if allowed:
                logger.info("Semantic privacy arbiter allowed source-specific lexical overlap.")
            return allowed
        except Exception as e:
            logger.warning(f"Semantic privacy arbiter failed closed: {e}")
            return False

    def _repair_joint_response(
        self,
        messages_history: List[Dict],
        reasons: List[str],
        retry: bool = False,
        extra_instruction: str = ""
    ) -> str:
        """Generate a fresh privacy-safe joint reply from joint transcript only."""
        transcript = format_messages_for_llm(messages_history[-12:])
        retry_instruction = extra_instruction.strip()
        if retry:
            retry_instruction = " ".join(filter(None, [
                retry_instruction,
                "This is a retry because the first repair still failed. Be stricter: "
                "do not mention what the therapist knows, heard, was told, can say, "
                "cannot say, can confirm, or cannot confirm. Do not repeat guessed "
                "sensitive-topic words unless the subject partner named them."
            ]))
        prompt = JOINT_RESPONSE_REPAIR_PROMPT_TEMPLATE.format(
            joint_transcript=transcript,
            reasons=", ".join(self._safe_repair_reason_labels(reasons)),
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
    def _safe_repair_reason_labels(reasons: List[str]) -> List[str]:
        """
        Convert validator reasons into non-sensitive labels before sending them
        to the repair LLM. The raw labels can name hidden categories such as
        infidelity; the repair model should only know what kind of correction
        to make, not the private topic that triggered it.
        """
        safe_labels = []
        for reason in reasons or []:
            if "too_generic" in reason or "low_signal" in reason:
                safe_labels.append("response_too_generic")
            elif "source_specific" in reason:
                safe_labels.append("response_used_unintroduced_specifics")
            elif "source" in reason:
                safe_labels.append("response_referred_to_information_source")
            elif "acknowledgement" in reason:
                safe_labels.append("response_missed_live_disclosure")
            else:
                safe_labels.append("response_crossed_privacy_boundary")

        return list(dict.fromkeys(safe_labels or ["response_crossed_privacy_boundary"]))

    @staticmethod
    def _should_guard_private_context(context_data: Dict) -> bool:
        """
        Use the expensive lexical privacy backstop only for sensitive private
        memory. Low-sensitivity private context often contains ordinary therapy
        language; comparing every generated reply against every low-level word
        creates false positives and causes generic fallback responses.
        """
        if not context_data:
            return False

        try:
            secret_level = int(context_data.get("secret_level", 0) or 0)
        except (TypeError, ValueError):
            secret_level = 0
        if secret_level >= 7:
            return True

        searchable = " ".join([
            str(context_data.get("text") or ""),
            str(context_data.get("category") or ""),
            " ".join(str(tag) for tag in (context_data.get("tags") or [])),
        ])
        return bool(PrivacyBoundaryService.detect_sensitive_topics([searchable]))

    @staticmethod
    def _joint_privacy_circuit_breaker_response() -> str:
        """
        Last-resort response used only if model repair fails validation or errors.
        Runtime should usually take the repair path so the reply remains contextual.
        """
        return (
            "I want to keep this grounded in the conversation you are having together. "
            "Pause for a moment: one partner can say what they need understood, and the other can "
            "reflect back what they heard before responding. What needs to be understood first?"
        )

    @classmethod
    def _is_circuit_breaker_response(cls, text: str) -> bool:
        return " ".join((text or "").split()) == " ".join(
            cls._joint_privacy_circuit_breaker_response().split()
        )

    @staticmethod
    def _is_low_signal_joint_response(text: str) -> bool:
        lowered = " ".join((text or "").lower().split())
        low_signal_phrases = (
            "what has been named here",
            "guessing or filling in blanks",
            "one small truth",
            "what feels possible right now",
            "what is the next sentence that feels possible",
            "what can you say here, in your own words",
            "ready to say here together",
        )
        return any(phrase in lowered for phrase in low_signal_phrases)

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
    def _source_seeking_boundary_response(messages_history: Optional[List[Dict]] = None) -> str:
        latest = ChatService._latest_user_message(messages_history)
        speaker = (latest or {}).get("sender_name") or "I"
        partner_name = ChatService._recent_other_user_name(messages_history, speaker) or "your partner"
        return (
            f"{speaker}, it makes sense to want a clear answer. Put the question directly to "
            f"{partner_name}, then give them room to answer in their own words. "
            f"{partner_name}, start with the part you can answer directly: what do you want "
            f"{speaker} to understand right now?"
        )

    @staticmethod
    def _privacy_safe_redirection_response(messages_history: Optional[List[Dict]] = None) -> str:
        latest = ChatService._latest_user_message(messages_history)

        speaker = (latest or {}).get("sender_name") or "I"
        content = ((latest or {}).get("content") or "").lower()
        partner_name = ChatService._recent_other_user_name(messages_history, speaker) or "your partner"

        if any(term in content for term in ("trust", "distant", "distance", "tense")):
            return (
                f"{speaker}, the trust and distance you are naming need a direct, careful response. "
                f"Ask for one observable thing you need from {partner_name}, and let "
                f"{partner_name} answer with what they can commit to in this conversation. "
                "What would help you feel less shut out today?"
            )

        if any(term in content for term in ("scared", "overwhelmed", "guilty", "accountability", "honesty")):
            return (
                f"{speaker}, I hear the pressure in wanting accountability without losing your footing. "
                f"Name the feeling first, then make one request of {partner_name} about how to listen. "
                "What kind of response would help you stay present while you speak?"
            )

        return (
            f"{speaker}, there is enough here to slow down and make the exchange more concrete. "
            f"Say the impact on you in one sentence, then ask {partner_name} for one response you can "
            "actually listen to. What do you need understood before this moves forward?"
        )

    @staticmethod
    def _latest_user_message(messages_history: Optional[List[Dict]] = None) -> Optional[Dict]:
        for message in reversed(messages_history or []):
            if message.get("sender_id") != "therapist":
                return message
        return None

    @staticmethod
    def _recent_other_user_name(
        messages_history: Optional[List[Dict]],
        speaker_name: Optional[str],
    ) -> Optional[str]:
        for message in reversed(messages_history or []):
            if message.get("sender_id") == "therapist":
                continue
            candidate = message.get("sender_name")
            if candidate and candidate != speaker_name:
                return candidate
        return None

    @staticmethod
    def _contains_any_term(text: str, terms: List[str]) -> bool:
        lowered = (text or "").lower()
        return any(term.lower() in lowered for term in terms)

    @staticmethod
    def _prepend_live_disclosure_acknowledgement(text: str, term: str) -> str:
        term = " ".join((term or "").split())
        if not term:
            return text
        return f'You just said "{term}" out loud. {text}'

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
