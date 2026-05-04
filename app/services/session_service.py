"""
Session Service

Manages therapy session lifecycle.
Handles creation, ending, and conclusion of sessions.
"""

from sqlalchemy.orm import Session
from app.db.models import Session as SessionModel, Message, CheckIn
from app.services.context_service import ContextService
from app.services.checkin_service import CheckInService
from app.services.therapist_notes_service import TherapistNotesService
from app.utils.logger import get_logger
import uuid
from datetime import datetime, timezone
from typing import Dict, List

logger = get_logger(__name__)


def _parse_iso_datetime(value: str) -> datetime:
    """Parse frontend ISO datetimes into naive UTC for DB storage."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


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
        self.notes_service = TherapistNotesService(db)

    def create_session(
        self,
        group_id: str,
        session_type: str,
        created_by: str,
        participants: List[str],
        scheduled_for: str = None,
        invite_message: str = None
    ) -> SessionModel:
        """
        Create a new therapy session.

        Args:
            group_id: UUID of the relationship (optional for private sessions)
            session_type: 'private' or 'joint'
            created_by: UUID of user creating the session
            participants: List of participant UUIDs
            scheduled_for: Optional ISO datetime string for scheduled sessions
            invite_message: Optional message shown to the invited partner

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
            scheduled_for=_parse_iso_datetime(scheduled_for) if scheduled_for else None,
            invite_message=invite_message,
            created_at=datetime.utcnow()
        )

        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)

        return session

    def join_session(
        self,
        session_id: str,
        user_id: str
    ) -> SessionModel:
        """
        Mark a joint waiting-room session active when an invited participant joins.
        """
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        participant = uuid.UUID(user_id)
        if participant not in session.participants:
            raise ValueError("Not authorized to join this session")

        if session.type != 'joint':
            raise ValueError("Only joint sessions can be joined")

        if session.status in {'ending', 'pending_actions', 'concluded'}:
            raise ValueError(f"Cannot join a session with status {session.status}")

        if session.status != 'active':
            session.status = 'active'
            session.started_at = session.started_at or datetime.utcnow()

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

        requester = uuid.UUID(user_id)
        if requester not in session.participants:
            raise ValueError("Not authorized to end this session")

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
        user_id: str,
        process_post_session: bool = True
    ) -> Dict:
        """
        End the session and optionally run post-session processing.

        Relationship-scoped sessions can need multiple LLM calls to extract
        memories, redacted joint guidance, and check-ins. Callers that need a
        fast HTTP response can set process_post_session=False, which records
        the end immediately and lets a background worker finish processing.

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

        requester = uuid.UUID(user_id)
        if requester not in session.participants:
            raise ValueError("Not authorized to end this session")

        if session.status in {'concluded', 'pending_actions'}:
            return {
                'session_id': str(session.id),
                'status': session.status,
                'contexts_extracted': 0,
                'check_ins_proposed': 0,
                'post_processing_required': False
            }

        # Set to ending status (locked for processing).
        session.status = 'ending'
        if not session.ended_at:
            session.ended_at = datetime.utcnow()
        self.db.commit()

        if not process_post_session and session.group_id is not None:
            return {
                'session_id': str(session.id),
                'status': session.status,
                'contexts_extracted': 0,
                'check_ins_proposed': 0,
                'post_processing_required': True
            }

        return await self.process_ended_session(session_id)

    async def process_ended_session(self, session_id: str) -> Dict:
        """
        Run post-session processing for a session already marked as ending.

        This may perform LLM work and should usually run outside the request
        path. Completion errors are logged and the session remains ended so the
        user is not trapped in an active session.
        """
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()

        if not session:
            raise ValueError(f"Session {session_id} not found")

        if session.status in {'concluded', 'pending_actions'}:
            return {
                'session_id': str(session.id),
                'status': session.status,
                'contexts_extracted': 0,
                'check_ins_proposed': 0,
                'post_processing_required': False
            }

        if not session.ended_at:
            session.ended_at = datetime.utcnow()
            self.db.commit()

        try:
            # Skip context extraction and check-ins for solo private sessions
            # (no relationship/partner yet)
            if session.group_id is None:
                session.status = 'concluded'
                self.db.commit()
                try:
                    self.notes_service.create_summary_note(
                        session_id=session_id,
                        scope=session.type,
                        summary_payload={
                            "session_id": str(session.id),
                            "contexts_extracted": 0,
                            "checkins_proposed": 0,
                            "ended_at": session.ended_at.isoformat() if session.ended_at else None
                        }
                    )
                except Exception:
                    pass
                return {
                    'session_id': str(session.id),
                    'status': session.status,
                    'contexts_extracted': 0,
                    'check_ins_proposed': 0,
                    'post_processing_required': False
                }

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

            try:
                self.notes_service.create_summary_note(
                    session_id=session_id,
                    scope=session.type,
                    summary_payload={
                        "session_id": str(session.id),
                        "contexts_extracted": len(extracted_data.get('user_a_contexts', [])) +
                                            len(extracted_data.get('user_b_contexts', [])) +
                                            len(extracted_data.get('group_contexts', [])),
                        "checkins_proposed": len(check_ins),
                        "ended_at": session.ended_at.isoformat() if session.ended_at else None
                    }
                )
            except Exception:
                # Summary note failures should not break session completion.
                pass

            return {
                'session_id': str(session.id),
                'status': session.status,
                'contexts_extracted': len(extracted_data.get('user_a_contexts', [])) +
                                     len(extracted_data.get('user_b_contexts', [])) +
                                     len(extracted_data.get('group_contexts', [])),
                'check_ins_proposed': len(check_ins),
                'post_processing_required': False
            }

        except Exception as e:
            logger.error(f"Session end post-processing failed for {session_id}: {e}", exc_info=True)
            self.db.rollback()
            session = self.db.query(SessionModel).filter(
                SessionModel.id == uuid.UUID(session_id)
            ).first()
            if not session:
                raise

            # The user ended the session successfully; do not re-open it just
            # because extraction failed.
            session.status = 'concluded'
            if not session.ended_at:
                session.ended_at = datetime.utcnow()
            self.db.commit()

            try:
                self.notes_service.create_summary_note(
                    session_id=session_id,
                    scope=session.type,
                    summary_payload={
                        "session_id": str(session.id),
                        "contexts_extracted": 0,
                        "checkins_proposed": 0,
                        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                        "post_processing_error": str(e)
                    }
                )
            except Exception:
                pass

            return {
                'session_id': str(session.id),
                'status': session.status,
                'contexts_extracted': 0,
                'check_ins_proposed': 0,
                'post_processing_required': False,
                'post_processing_error': str(e)
            }

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
            SessionModel.participants.any(uuid.UUID(user_id))
        ).all()
