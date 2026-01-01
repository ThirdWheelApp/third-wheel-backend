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
    GroupContext
)
from anthropic import Anthropic
from app.config.settings import settings
from app.config.prompts import (
    CONTEXT_EXTRACTION_SYSTEM_PROMPT,
    CONTEXT_EXTRACTION_PROMPT_TEMPLATE,
    format_messages_for_llm
)
import uuid
import json
from datetime import datetime
from typing import Dict, List


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
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

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

            # Parse JSON response
            # Try to extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                extracted_data = json.loads(json_match.group())
            else:
                print(f"Could not parse JSON from LLM response: {response_text}")
                extracted_data = {
                    'user_a_contexts': [],
                    'user_b_contexts': [],
                    'group_contexts': [],
                    'check_ins': []
                }

            # Save contexts to database
            await self._save_contexts(
                session,
                extracted_data
            )

            return extracted_data

        except Exception as e:
            print(f"Error extracting context: {e}")
            return {
                'user_a_contexts': [],
                'user_b_contexts': [],
                'group_contexts': [],
                'check_ins': [],
                'error': str(e)
            }

    async def _save_contexts(
        self,
        session: SessionModel,
        extracted_data: Dict
    ):
        """Save extracted contexts to database."""

        # Save private contexts for User A
        if len(session.participants) >= 1:
            user_a_id = session.participants[0]
            for ctx_data in extracted_data.get('user_a_contexts', []):
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

        # Save private contexts for User B
        if len(session.participants) >= 2:
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

        # Save group contexts
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
