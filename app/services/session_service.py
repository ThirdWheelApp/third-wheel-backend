"""
Session Service

Manages therapy session lifecycle.
Handles creation, ending, and conclusion of sessions.
"""

from sqlalchemy.orm import Session
from app.db.models import Session as SessionModel, Message, CheckIn
from app.services.context_service import ContextService
from app.services.checkin_service import CheckInService
import uuid
from datetime import datetime
from typing import Dict, List


class SessionService:
    """
    Service for managing therapy session lifecycle.

    Session Status Flow:
    - active: Session in progress
    - pending_conclusion: One user clicked "End Session"
    - ending: Both users agreed, processing context
    - pending_actions: Waiting for check-in approvals
    - concluded: Fully completed
    """

    def __init__(self, db: Session):
        self.db = db
        self.context_service = ContextService(db)
        self.checkin_service = CheckInService(db)

    def create_session(
        self,
        group_id: str,
        session_type: str,
        created_by: str,
        participants: List[str],
        scheduled_for: str = None
    ) -> SessionModel:
        """
        Create a new therapy session.

        Args:
            group_id: UUID of the relationship (optional for private sessions)
            session_type: 'private' or 'joint'
            created_by: UUID of user creating the session
            participants: List of participant UUIDs
            scheduled_for: Optional ISO datetime string for scheduled sessions

        Returns:
            Created session object
        """
        session = SessionModel(
            id=uuid.uuid4(),
            group_id=uuid.UUID(group_id) if group_id else None,  # Optional for private sessions
            type=session_type,
            status='active' if not scheduled_for else 'scheduled',
            created_by=uuid.UUID(created_by),
            participants=[uuid.UUID(p) for p in participants],
            current_context={},
            started_at=datetime.utcnow() if not scheduled_for else None,
            scheduled_for=datetime.fromisoformat(scheduled_for) if scheduled_for else None,
            created_at=datetime.utcnow()
        )

        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)

        return session

    async def request_end_session(
        self,
        session_id: str,
        user_id: str
    ) -> Dict:
        """
        First user requests to end the session.

        Sets status to 'pending_conclusion' and records who requested.

        Args:
            session_id: UUID of the session
            user_id: UUID of user requesting to end

        Returns:
            Dictionary with session status
        """
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Update status
        session.status = 'pending_conclusion'
        session.end_requested_by = uuid.UUID(user_id)

        self.db.commit()

        return {
            'session_id': str(session.id),
            'status': session.status,
            'end_requested_by': str(session.end_requested_by)
        }

    async def end_session(
        self,
        session_id: str,
        user_id: str
    ) -> Dict:
        """
        Second user confirms ending the session.

        Triggers post-session processing:
        1. Extract context
        2. Extract check-ins
        3. Set status to 'pending_actions'

        Args:
            session_id: UUID of the session
            user_id: UUID of user confirming end

        Returns:
            Dictionary with extracted data
        """
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Set to ending status (locked for processing)
        session.status = 'ending'
        session.ended_at = datetime.utcnow()
        self.db.commit()

        try:
            # Extract context from session
            extracted_data = await self.context_service.extract_context_from_session(
                session_id
            )

            # Create check-ins from extracted data
            check_ins = await self.checkin_service.create_checkins_from_extraction(
                session_id,
                str(session.group_id),
                extracted_data.get('check_ins', [])
            )

            # Update session status
            session.status = 'pending_actions' if check_ins else 'concluded'
            self.db.commit()

            return {
                'session_id': str(session.id),
                'status': session.status,
                'contexts_extracted': len(extracted_data.get('user_a_contexts', [])) +
                                     len(extracted_data.get('user_b_contexts', [])) +
                                     len(extracted_data.get('group_contexts', [])),
                'check_ins_proposed': len(check_ins)
            }

        except Exception as e:
            # Revert to active on error
            session.status = 'active'
            self.db.commit()
            raise e

    def conclude_session(self, session_id: str):
        """
        Mark session as concluded.

        Called after all check-ins are approved.

        Args:
            session_id: UUID of the session
        """
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.status = 'concluded'
        self.db.commit()

    def get_session(self, session_id: str) -> SessionModel:
        """Get session by ID."""
        return self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

    def get_pending_action_sessions(self, user_id: str) -> List[SessionModel]:
        """Get sessions with pending actions for a user."""
        return self.db.query(SessionModel).filter(
            SessionModel.status == 'pending_actions',
            SessionModel.participants.contains([uuid.UUID(user_id)])
        ).all()
