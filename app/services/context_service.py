"""
Context Service

Handles context extraction from therapy sessions and storage.
Extracts insights about users and relationships using LLM analysis.
"""

from sqlalchemy.orm import Session
from app.db.models import (
    Session as SessionModel,
    Message,
    PrivateUserContext,
    GroupContext,
    JointGuidanceContext
)
from app.config.settings import settings
from app.demo import get_llm_client
from app.config.prompts import (
    CONTEXT_EXTRACTION_SYSTEM_PROMPT,
    CONTEXT_EXTRACTION_PROMPT_TEMPLATE,
    JOINT_GUIDANCE_EXTRACTION_SYSTEM_PROMPT,
    JOINT_GUIDANCE_EXTRACTION_PROMPT_TEMPLATE,
    format_messages_for_llm
)
from app.services.privacy_boundary_service import PrivacyBoundaryService
from app.utils.logger import get_logger
import uuid
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

logger = get_logger(__name__)


# Secret level indicators in user messages
# Phrases that indicate higher sensitivity levels
SECRET_INDICATORS = [
    ("super secret", 9),
    ("don't tell anyone", 9),
    ("this is a secret", 8),
    ("please don't share", 8),
    ("between us", 7),
    ("don't tell", 7),
    ("keep this private", 7),
    ("confidential", 6),
    ("private", 6),
    ("just between you and me", 8),
]


def detect_secret_level_boost(message: str) -> int:
    """
    Detect if user message indicates sensitivity.

    Scans message for phrases that indicate the user wants
    information to be kept private.

    Args:
        message: User's message text

    Returns:
        Secret level boost (0-9), 0 if no indicators found
    """
    message_lower = message.lower()
    max_boost = 0

    for phrase, level in SECRET_INDICATORS:
        if phrase in message_lower:
            max_boost = max(max_boost, level)

    return max_boost


def extract_json_from_response(response_text: str) -> Optional[Dict]:
    """
    Extract JSON from LLM response text.

    Handles multiple formats:
    1. Raw JSON object
    2. JSON in markdown code blocks (```json ... ```)
    3. JSON embedded in text

    Args:
        response_text: Raw response from LLM

    Returns:
        Parsed JSON dictionary or None if parsing fails
    """
    # Try 1: Direct JSON parse
    try:
        return json.loads(response_text.strip())
    except json.JSONDecodeError:
        pass

    # Try 2: Extract from markdown code block
    code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try 3: Find JSON object in text (greedy match for outermost braces)
    # Use a more careful approach - count braces to find complete JSON
    start_idx = response_text.find('{')
    if start_idx == -1:
        return None

    brace_count = 0
    end_idx = start_idx

    for i, char in enumerate(response_text[start_idx:], start=start_idx):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                end_idx = i + 1
                break

    if brace_count == 0:
        try:
            return json.loads(response_text[start_idx:end_idx])
        except json.JSONDecodeError:
            pass

    return None


class ContextService:
    """
    Service for extracting and managing context from therapy sessions.

    Post-session processing:
    1. Analyze all messages in the session
    2. Extract private contexts for each user (with secret levels)
    3. Extract shared group contexts
    4. Save to database
    """

    def __init__(self, db: Session):
        self.db = db
        self.client = get_llm_client()

    async def extract_context_from_session(self, session_id: str) -> Dict:
        """
        Extract all context from a concluded session.

        This is called during post-session processing after users
        click "End Session".

        Args:
            session_id: UUID of the session

        Returns:
            Dictionary with extracted contexts
        """
        # Get session
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Get all messages
        messages = self.db.query(Message).filter(
            Message.session_id == uuid.UUID(session_id)
        ).order_by(Message.sequence_number).all()

        # Format for LLM
        transcript = format_messages_for_llm([
            {
                'sender_name': m.sender_name,
                'content': m.content
            }
            for m in messages
        ])

        current_context = session.current_context or {}

        # Build extraction prompt
        prompt = CONTEXT_EXTRACTION_PROMPT_TEMPLATE.format(
            transcript=transcript,
            current_context=json.dumps(current_context, indent=2)
        )

        try:
            # Call LLM to extract contexts
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=2048,  # More tokens for extraction
                temperature=0.3,  # Lower temperature for structured output
                system=CONTEXT_EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text

            # Parse JSON response using robust extraction
            extracted_data = extract_json_from_response(response_text)

            if extracted_data is None:
                logger.error(f"Could not parse JSON from LLM response: {response_text[:500]}...")
                extracted_data = {
                    'user_a_contexts': [],
                    'user_b_contexts': [],
                    'group_contexts': [],
                    'check_ins': []
                }

            # Apply secret level boosts based on user messages
            self._apply_secret_level_boosts(extracted_data, messages)

            # Enforce session-type data boundaries before persisting.
            extracted_data = self._normalize_extracted_data_for_session(
                session=session,
                extracted_data=extracted_data
            )

            # Save contexts to database
            await self._save_contexts(
                session,
                extracted_data
            )

            return extracted_data

        except Exception as e:
            logger.error(f"Error extracting context: {e}", exc_info=True)
            return {
                'user_a_contexts': [],
                'user_b_contexts': [],
                'group_contexts': [],
                'check_ins': [],
                'error': str(e)
            }

    def _normalize_extracted_data_for_session(
        self,
        session: SessionModel,
        extracted_data: Dict
    ) -> Dict:
        """
        Enforce data boundaries by session type.

        POC privacy policy:
        - Private sessions can write private user context only.
        - Group context and check-ins are created from joint sessions only.
        """
        normalized = {
            'user_a_contexts': extracted_data.get('user_a_contexts', []) or [],
            'user_b_contexts': extracted_data.get('user_b_contexts', []) or [],
            'group_contexts': extracted_data.get('group_contexts', []) or [],
            'check_ins': extracted_data.get('check_ins', []) or [],
        }

        if session.type == 'private':
            merged_private_contexts = (
                list(normalized['user_a_contexts']) +
                list(normalized['user_b_contexts'])
            )
            normalized['user_a_contexts'] = merged_private_contexts
            normalized['user_b_contexts'] = []
            normalized['group_contexts'] = []
            normalized['check_ins'] = []

        return normalized

    def _apply_secret_level_boosts(self, extracted_data: Dict, messages: List) -> None:
        """
        Apply secret level boosts based on user message indicators.

        If user said "this is a secret", boost the secret level of
        contexts extracted from that conversation.
        """
        # Find max secret level boost from messages
        max_boost = 0
        for msg in messages:
            if msg.sender_id != 'therapist':
                boost = detect_secret_level_boost(msg.content)
                max_boost = max(max_boost, boost)

        # Apply boost to extracted contexts
        if max_boost > 0:
            for ctx in extracted_data.get('user_a_contexts', []):
                current = ctx.get('secret_level', 5)
                ctx['secret_level'] = min(10, max(current, max_boost))

            for ctx in extracted_data.get('user_b_contexts', []):
                current = ctx.get('secret_level', 5)
                ctx['secret_level'] = min(10, max(current, max_boost))

    async def _save_contexts(
        self,
        session: SessionModel,
        extracted_data: Dict
    ):
        """Save extracted contexts to database."""
        try:
            # Save private contexts for User A
            if len(session.participants) >= 1:
                user_a_id = session.participants[0]
                user_a_contexts = extracted_data.get('user_a_contexts', [])
                for ctx_data in user_a_contexts:
                    context = PrivateUserContext(
                        user_id=user_a_id,
                        group_id=session.group_id,
                        context_id=uuid.uuid4(),
                        data={
                            **ctx_data,
                            'created_at': datetime.utcnow().isoformat(),
                            'source_session_id': str(session.id)
                        },
                        created_at=datetime.utcnow()
                    )
                    self.db.add(context)

                if session.type == 'private' and session.group_id and user_a_contexts:
                    guidance_data = await self._derive_joint_guidance_for_private_contexts(
                        user_a_contexts
                    )
                    guidance = JointGuidanceContext(
                        id=uuid.uuid4(),
                        group_id=session.group_id,
                        subject_user_id=user_a_id,
                        source_session_id=session.id,
                        data={
                            **guidance_data,
                            'created_at': datetime.utcnow().isoformat(),
                            'source_session_id': str(session.id)
                        },
                        active=True,
                        created_at=datetime.utcnow()
                    )
                    self.db.add(guidance)

            # Save private contexts for User B (joint sessions only)
            if session.type == 'joint' and len(session.participants) >= 2:
                user_b_id = session.participants[1]
                for ctx_data in extracted_data.get('user_b_contexts', []):
                    context = PrivateUserContext(
                        user_id=user_b_id,
                        group_id=session.group_id,
                        context_id=uuid.uuid4(),
                        data={
                            **ctx_data,
                            'created_at': datetime.utcnow().isoformat(),
                            'source_session_id': str(session.id)
                        },
                        created_at=datetime.utcnow()
                    )
                    self.db.add(context)

            # Save group contexts (joint sessions only)
            if session.type == 'joint':
                for ctx_data in extracted_data.get('group_contexts', []):
                    context = GroupContext(
                        group_id=session.group_id,
                        context_id=uuid.uuid4(),
                        data={
                            **ctx_data,
                            'created_at': datetime.utcnow().isoformat(),
                            'source_session_id': str(session.id),
                            'participants': [str(p) for p in session.participants]
                        },
                        created_at=datetime.utcnow()
                    )
                    self.db.add(context)

            self.db.commit()
            logger.info(f"Saved contexts for session {session.id}")

        except Exception as e:
            logger.error(f"Error saving contexts: {e}", exc_info=True)
            self.db.rollback()
            raise

    async def _derive_joint_guidance_for_private_contexts(
        self,
        private_contexts: List[Dict]
    ) -> Dict:
        """
        Convert private raw memory into redacted joint-session guidance.

        The LLM is allowed to see raw private contexts here because this is a
        private-session post-processing step. Its output is privacy-guarded
        before being stored for joint-session use.
        """
        fallback = PrivacyBoundaryService.build_fallback_joint_guidance(private_contexts)
        if not private_contexts:
            return fallback

        prompt = JOINT_GUIDANCE_EXTRACTION_PROMPT_TEMPLATE.format(
            private_contexts=json.dumps(private_contexts, indent=2)
        )

        try:
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=700,
                temperature=0.2,
                system=JOINT_GUIDANCE_EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            payload = extract_json_from_response(response.content[0].text) or {}
            return PrivacyBoundaryService.sanitize_joint_guidance(
                payload,
                source_contexts=private_contexts
            )
        except Exception as e:
            logger.warning(f"Failed to derive model joint guidance, using fallback: {e}")
            return fallback
