"""
Therapist Notes Service

Maintains hidden therapist notes used for backend-only continuity.
These notes must never be sent to clients.
"""

from sqlalchemy.orm import Session
from app.db.models import TherapistNote
from app.utils.logger import get_logger
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = get_logger(__name__)


class TherapistNotesService:
    """CRUD helpers for internal therapist notes."""

    def __init__(self, db: Session):
        self.db = db

    def get_recent_note_context(
        self,
        session_id: str,
        scope: str,
        limit: int = 4
    ) -> str:
        """
        Return a compact note context string for continuity prompts.
        """
        notes = (
            self.db.query(TherapistNote)
            .filter(
                TherapistNote.session_id == uuid.UUID(session_id),
                TherapistNote.scope == scope,
                TherapistNote.note_type == "turn"
            )
            .order_by(TherapistNote.turn_sequence.desc())
            .limit(limit)
            .all()
        )

        if not notes:
            return ""

        notes.reverse()
        lines = []
        for note in notes:
            content = note.content or {}
            key_point = content.get("key_point", "")
            follow_up = content.get("follow_up", "")
            if key_point or follow_up:
                lines.append(f"- Key point: {key_point} | Follow-up: {follow_up}")

        return "\n".join(lines)

    def create_turn_note(
        self,
        session_id: str,
        scope: str,
        turn_sequence: int,
        user_id: Optional[str],
        user_message: str,
        therapist_message: str,
        task_proposals: Optional[List[Dict]] = None
    ) -> None:
        """
        Persist per-turn hidden note.
        """
        task_count = len(task_proposals or [])
        note_payload = {
            "key_point": (user_message or "")[:220],
            "therapist_strategy": (therapist_message or "")[:240],
            "follow_up": "Explore emotions and commitment to next step.",
            "task_proposals_count": task_count,
            "timestamp": datetime.utcnow().isoformat(),
        }

        note = TherapistNote(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            turn_sequence=turn_sequence,
            note_type="turn",
            scope=scope,
            subject_user_id=uuid.UUID(user_id) if user_id else None,
            content=note_payload,
            privacy_tags=["internal_only"],
            created_at=datetime.utcnow()
        )

        self.db.add(note)
        self.db.commit()

    def create_summary_note(
        self,
        session_id: str,
        scope: str,
        summary_payload: Dict
    ) -> None:
        """
        Persist session-end summary hidden note.
        """
        # Use a high sequence number so summary appears at the end.
        note = TherapistNote(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            turn_sequence=999999,
            note_type="summary",
            scope=scope,
            subject_user_id=None,
            content=summary_payload or {},
            privacy_tags=["internal_only", "session_summary"],
            created_at=datetime.utcnow()
        )
        self.db.add(note)
        self.db.commit()

