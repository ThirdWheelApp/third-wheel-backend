"""
CheckIn Service

Manages accountability check-ins and their approval workflow.
"""

from sqlalchemy.orm import Session
from app.db.models import CheckIn, User, Session as SessionModel
from app.services.notification_service import NotificationService, NotificationType
from app.utils.logger import get_logger
import uuid
import re
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional

logger = get_logger(__name__)


class CheckInService:
    """
    Service for managing check-ins (accountability items).

    Check-in Status Flow:
    - proposed: Extracted from session, awaiting approval
    - rejected: Declined by assignee
    - active: Approved and being tracked
    - awaiting_verification: User marked done, waiting for verifier
    - needs_work: Verifier rejected
    - completed: Fully completed
    """

    def __init__(self, db: Session):
        self.db = db
        self.notification_service = NotificationService(db)

    async def create_checkins_from_extraction(
        self,
        session_id: str,
        group_id: str,
        checkins_data: List[Dict]
    ) -> List[CheckIn]:
        """
        Create check-ins from LLM extraction.

        Args:
            session_id: UUID of the session
            group_id: UUID of the relationship
            checkins_data: List of check-in dictionaries from context extraction

        Returns:
            List of created check-ins
        """
        created_checkins = []
        session = self.db.query(SessionModel).filter(
            SessionModel.id == uuid.UUID(session_id)
        ).first()
        participant_tokens = {}
        if session and len(session.participants) >= 1:
            participant_tokens["user_a"] = str(session.participants[0])
        if session and len(session.participants) >= 2:
            participant_tokens["user_b"] = str(session.participants[1])

        participant_ids = list(participant_tokens.values())

        for data in checkins_data:
            assigned_raw = data.get('assigned_to')
            verifier_raw = data.get('verifier')

            assigned_to = self._resolve_user_reference(assigned_raw, participant_tokens)
            verifier_id = self._resolve_user_reference(verifier_raw, participant_tokens) if verifier_raw else None
            if assigned_to and not verifier_id:
                verifier_id = self._other_participant_id(participant_ids, assigned_to)

            if not assigned_to:
                logger.warning(f"Skipping check-in with unresolved assignee: {data}")
                continue

            checkin = self.create_task_proposal(
                group_id=group_id,
                assigned_to=assigned_to,
                title=data.get('title'),
                description=data.get('description'),
                proposed_by=None,
                verifier_id=verifier_id,
                requires_verification=bool(data.get('requires_verification', False)),
                frequency=data.get('frequency', 'daily'),
                duration_days=data.get('duration_days', 7),
                session_id=session_id,
                commit=False
            )

            if checkin:
                created_checkins.append(checkin)

        self.db.commit()
        return created_checkins

    def create_task_proposal(
        self,
        group_id: str,
        assigned_to: str,
        title: str,
        description: Optional[str] = None,
        proposed_by: Optional[str] = None,
        verifier_id: Optional[str] = None,
        requires_verification: bool = False,
        frequency: str = "daily",
        duration_days: int = 7,
        session_id: Optional[str] = None,
        commit: bool = True
    ) -> Optional[CheckIn]:
        """
        Create a canonical relationship task proposal.

        The assigned user and the non-assigned partner both need to accept before
        a relationship task becomes active. ``verifier_id`` is therefore used for
        the partner's agreement even when completion verification is not required.
        """
        try:
            group_uuid = uuid.UUID(str(group_id))
            assigned_uuid = uuid.UUID(str(assigned_to))
            verifier_uuid = uuid.UUID(str(verifier_id)) if verifier_id else None
            proposer_uuid = uuid.UUID(str(proposed_by)) if proposed_by else None
            source_session_uuid = uuid.UUID(str(session_id)) if session_id else None
        except (TypeError, ValueError):
            logger.warning(
                "Skipping task proposal with invalid ids: "
                f"group={group_id}, assigned_to={assigned_to}, verifier={verifier_id}"
            )
            return None

        clean_title = self._clean_task_title(title)
        if not clean_title:
            logger.warning("Skipping task proposal without a title")
            return None

        existing = self._find_duplicate_task(
            group_uuid,
            assigned_uuid,
            clean_title,
            source_session_uuid
        )
        if existing:
            logger.info(f"Skipping duplicate task proposal {existing.id}: {clean_title}")
            return None

        normalized_frequency = self._normalize_frequency(frequency)
        if normalized_frequency != "one_time" and self._looks_one_time_task(clean_title, description):
            normalized_frequency = "one_time"
        normalized_duration = (
            1
            if normalized_frequency == "one_time"
            else self._normalize_duration_days(duration_days)
        )
        assigned_approved = proposer_uuid == assigned_uuid
        verifier_approved = True if verifier_uuid is None else proposer_uuid == verifier_uuid

        checkin = CheckIn(
            id=uuid.uuid4(),
            group_id=group_uuid,
            assigned_to=assigned_uuid,
            verifier_id=verifier_uuid,
            title=clean_title,
            description=(description or "").strip() or None,
            status='proposed',
            assigned_approved=assigned_approved,
            verifier_approved=verifier_approved,
            requires_verification=bool(requires_verification and verifier_uuid),
            frequency=normalized_frequency,
            progress={'completed': 0, 'total': normalized_duration},
            next_check_date=date.today() + timedelta(days=1),
            created_from_session=source_session_uuid,
            created_at=datetime.utcnow()
        )

        if self._is_fully_approved(checkin):
            checkin.status = 'active'

        self.db.add(checkin)
        if commit:
            self.db.commit()
            self.db.refresh(checkin)
        return checkin

    def _resolve_user_reference(
        self,
        raw_value: Optional[str],
        participant_tokens: Dict[str, str]
    ) -> Optional[str]:
        """Resolve user_a/user_b/UUID references from extraction output."""
        if not raw_value:
            return None
        value = str(raw_value).strip()
        if value in participant_tokens:
            return participant_tokens[value]
        try:
            return str(uuid.UUID(value))
        except Exception:
            return None

    async def approve_checkin(
        self,
        checkin_id: str,
        user_id: str,
        role: str  # 'assigned' or 'verifier'
    ) -> Dict:
        """
        Approve a check-in (either by assigned user or verifier).

        Args:
            checkin_id: UUID of the check-in
            user_id: UUID of the user approving
            role: Who is approving ('assigned' or 'verifier')

        Returns:
            Dictionary with updated check-in status
        """
        checkin = self.db.query(CheckIn).filter(
            CheckIn.id == uuid.UUID(checkin_id)
        ).first()

        if not checkin:
            raise ValueError(f"Check-in {checkin_id} not found")

        if checkin.status != 'proposed':
            raise ValueError("Check-in is no longer awaiting approval")

        if role == 'assigned':
            if str(checkin.assigned_to) != user_id:
                raise ValueError("Only the assigned user can approve as assignee")
            checkin.assigned_approved = True
        elif role == 'verifier':
            if not checkin.verifier_id:
                raise ValueError("This check-in does not require a verifier")
            if str(checkin.verifier_id) != user_id:
                raise ValueError("Only the designated verifier can approve as verifier")
            checkin.verifier_approved = True
        else:
            raise ValueError("Role must be 'assigned' or 'verifier'")

        # Activate if all required people have accepted.
        was_proposed = checkin.status == 'proposed'
        if self._is_fully_approved(checkin):
            checkin.status = 'active'

            # Notify assigned user that check-in is now active
            if was_proposed:
                approver = self.db.query(User).filter(User.id == uuid.UUID(user_id)).first()
                await self.notification_service.notify_check_in_assigned(
                    user_id=str(checkin.assigned_to),
                    check_in_id=str(checkin.id),
                    title=checkin.title,
                    assigned_by_name=approver.name if approver else "Partner"
                )

        checkin.updated_at = datetime.utcnow()
        self._conclude_source_session_if_actions_resolved(checkin)
        self.db.commit()

        return {
            'checkin_id': str(checkin.id),
            'status': checkin.status,
            'assigned_approved': checkin.assigned_approved,
            'verifier_approved': checkin.verifier_approved
        }

    async def mark_checkin_done(self, checkin_id: str, user_id: str) -> Dict:
        """
        User marks check-in as done for the day/period.

        Args:
            checkin_id: UUID of the check-in
            user_id: UUID of the user marking done

        Returns:
            Dictionary with updated progress
        """
        checkin = self.db.query(CheckIn).filter(
            CheckIn.id == uuid.UUID(checkin_id)
        ).first()

        if not checkin:
            raise ValueError(f"Check-in {checkin_id} not found")

        if str(checkin.assigned_to) != user_id:
            raise ValueError("Only the assigned user can mark this check-in done")

        if checkin.status not in {'active', 'needs_work'}:
            raise ValueError("Check-in is not currently active")

        # Increment completed count
        progress = dict(checkin.progress or {'completed': 0, 'total': 7})
        progress['completed'] = min(
            int(progress.get('completed', 0)) + 1,
            int(progress.get('total', 7))
        )
        checkin.progress = progress

        history = list(checkin.completion_history or [])
        history.append({
            'date': date.today().isoformat(),
            'completed': True
        })
        checkin.completion_history = history

        # Update status based on verification requirement
        if checkin.requires_verification and checkin.verifier_id:
            checkin.status = 'awaiting_verification'

            # Notify verifier that check-in needs verification
            if checkin.verifier_id:
                user = self.db.query(User).filter(User.id == uuid.UUID(user_id)).first()
                await self.notification_service.notify_check_in_verification_needed(
                    verifier_id=str(checkin.verifier_id),
                    check_in_id=str(checkin.id),
                    title=checkin.title,
                    completed_by_name=user.name if user else "Partner"
                )
        elif progress['completed'] >= progress['total']:
            checkin.status = 'completed'
        else:
            checkin.status = 'active'

        # Update next check date
        if checkin.frequency == 'daily':
            checkin.next_check_date = date.today() + timedelta(days=1)
        elif checkin.frequency == 'weekly':
            checkin.next_check_date = date.today() + timedelta(days=7)

        checkin.updated_at = datetime.utcnow()
        self.db.commit()

        return {
            'checkin_id': str(checkin.id),
            'status': checkin.status,
            'progress': checkin.progress
        }

    async def verify_checkin(
        self,
        checkin_id: str,
        verified_by: str,
        status: str,  # 'verified' or 'needs_work'
        feedback: str = None
    ) -> Dict:
        """
        Verifier approves or rejects a check-in completion.

        Args:
            checkin_id: UUID of the check-in
            verified_by: UUID of verifier
            status: 'verified' or 'needs_work'
            feedback: Optional feedback if needs work

        Returns:
            Dictionary with updated check-in
        """
        checkin = self.db.query(CheckIn).filter(
            CheckIn.id == uuid.UUID(checkin_id)
        ).first()

        if not checkin:
            raise ValueError(f"Check-in {checkin_id} not found")

        if not checkin.verifier_id:
            raise ValueError("This check-in does not require verification")

        if str(checkin.verifier_id) != verified_by:
            raise ValueError("Only the designated verifier can verify this check-in")

        if checkin.status != 'awaiting_verification':
            raise ValueError("Check-in is not awaiting verification")

        status_normalized = (status or "").strip().lower()
        if status_normalized not in {'verified', 'needs_work'}:
            raise ValueError("Status must be 'verified' or 'needs_work'")

        verifier = self.db.query(User).filter(User.id == uuid.UUID(verified_by)).first()

        if status_normalized == 'verified':
            progress = checkin.progress or {'completed': 0, 'total': 7}
            if int(progress.get('completed', 0)) >= int(progress.get('total', 7)):
                checkin.status = 'completed'
            else:
                # Reset to active for ongoing tracking
                checkin.status = 'active'
            checkin.verification_feedback = None

            # Notify assigned user of approval
            await self.notification_service.create_notification(
                user_id=str(checkin.assigned_to),
                notification_type=NotificationType.CHECK_IN_VERIFIED,
                data={
                    'checkInId': str(checkin.id),
                    'title': checkin.title,
                    'verifiedByName': verifier.name if verifier else "Partner",
                    'message': f"Your check-in '{checkin.title}' was verified!"
                }
            )
        else:  # needs_work
            checkin.status = 'needs_work'
            checkin.verification_feedback = feedback or "Please try again"
            # Decrement progress since it wasn't verified
            progress = dict(checkin.progress or {})
            if progress and progress.get('completed', 0) > 0:
                progress['completed'] -= 1
                checkin.progress = progress

            # Notify assigned user that check-in needs work
            await self.notification_service.create_notification(
                user_id=str(checkin.assigned_to),
                notification_type=NotificationType.CHECK_IN_NEEDS_WORK,
                data={
                    'checkInId': str(checkin.id),
                    'title': checkin.title,
                    'verifiedByName': verifier.name if verifier else "Partner",
                    'feedback': checkin.verification_feedback,
                    'message': f"Your check-in '{checkin.title}' needs more work"
                }
            )

        checkin.updated_at = datetime.utcnow()
        self.db.commit()

        return {
            'checkin_id': str(checkin.id),
            'status': checkin.status,
            'feedback': checkin.verification_feedback
        }

    def get_proposed_checkins_for_session(
        self,
        session_id: str
    ) -> List[CheckIn]:
        """Get all proposed check-ins from a session."""
        return self.db.query(CheckIn).filter(
            CheckIn.created_from_session == uuid.UUID(session_id),
            CheckIn.status == 'proposed'
        ).all()

    def get_active_checkins_for_user(
        self,
        group_id: str,
        user_id: str
    ) -> List[CheckIn]:
        """
        Get active check-ins for a user in a group.

        Returns check-ins where user is assigned_to OR verifier_id.
        """
        user_uuid = uuid.UUID(user_id)
        group_uuid = uuid.UUID(group_id)

        return self.db.query(CheckIn).filter(
            CheckIn.group_id == group_uuid,
            CheckIn.status.in_(['active', 'awaiting_verification', 'needs_work']),
            (CheckIn.assigned_to == user_uuid) | (CheckIn.verifier_id == user_uuid)
        ).order_by(CheckIn.next_check_date).all()

    def get_checkin(self, checkin_id: str) -> Optional[CheckIn]:
        """Get check-in by ID."""
        return self.db.query(CheckIn).filter(
            CheckIn.id == uuid.UUID(checkin_id)
        ).first()

    def get_tasks_for_group(
        self,
        group_id: str
    ) -> List[CheckIn]:
        """POC tasks list endpoint backed by check-ins."""
        return self.db.query(CheckIn).filter(
            CheckIn.group_id == uuid.UUID(group_id),
            CheckIn.status.in_([
                'proposed',
                'active',
                'awaiting_verification',
                'needs_work',
                'completed',
                'rejected'
            ])
        ).order_by(CheckIn.created_at.desc()).all()

    async def decide_task(
        self,
        checkin_id: str,
        user_id: str,
        decision: str,
        reason: Optional[str] = None
    ) -> Dict:
        """
        Mutual decision flow for proposed relationship tasks.

        Assignees accept the work they are taking on. The partner/verifier
        accepts that this is an agreed relationship task. The task only becomes
        active after all required approvals are present.
        """
        checkin = self.db.query(CheckIn).filter(
            CheckIn.id == uuid.UUID(checkin_id)
        ).first()

        if not checkin:
            raise ValueError(f"Task {checkin_id} not found")

        actor_role = None
        if str(checkin.assigned_to) == user_id:
            actor_role = "assigned"
        elif checkin.verifier_id and str(checkin.verifier_id) == user_id:
            actor_role = "verifier"

        if actor_role is None:
            raise ValueError("Only task participants can accept/reject this task")

        if checkin.status != 'proposed':
            raise ValueError("Task is no longer awaiting decision")

        decision_normalized = (decision or "").strip().lower()
        if decision_normalized not in {'accepted', 'rejected'}:
            raise ValueError("Decision must be 'accepted' or 'rejected'")

        if decision_normalized == 'accepted':
            if actor_role == "assigned":
                checkin.assigned_approved = True
            else:
                checkin.verifier_approved = True

            if self._is_fully_approved(checkin):
                checkin.status = 'active'
                message = "Task accepted and activated"
            else:
                message = "Task accepted; waiting for partner"
        else:
            checkin.status = 'rejected'
            checkin.verification_feedback = reason or f"Rejected by {actor_role}"
            message = "Task rejected"

        checkin.updated_at = datetime.utcnow()
        self._conclude_source_session_if_actions_resolved(checkin)
        self.db.commit()
        self.db.refresh(checkin)

        return {
            'task_id': str(checkin.id),
            'status': checkin.status,
            'assigned_approved': checkin.assigned_approved,
            'verifier_approved': checkin.verifier_approved,
            'requires_verification': checkin.requires_verification,
            'decided_by_role': actor_role,
            'message': message
        }

    def _is_fully_approved(self, checkin: CheckIn) -> bool:
        """Check if check-in is fully approved."""
        if not checkin.assigned_approved:
            return False

        if checkin.verifier_id and not checkin.verifier_approved:
            return False

        return True

    def _conclude_source_session_if_actions_resolved(self, checkin: CheckIn) -> None:
        """Conclude a pending-actions session once no proposed tasks remain."""
        if not checkin.created_from_session:
            return

        session = self.db.query(SessionModel).filter(
            SessionModel.id == checkin.created_from_session
        ).first()
        if not session or session.status != 'pending_actions':
            return

        proposed_count = self.db.query(CheckIn).filter(
            CheckIn.created_from_session == checkin.created_from_session,
            CheckIn.status == 'proposed'
        ).count()
        if proposed_count == 0:
            session.status = 'concluded'

    @staticmethod
    def _other_participant_id(participant_ids: List[str], user_id: str) -> Optional[str]:
        for participant_id in participant_ids:
            if str(participant_id) != str(user_id):
                return str(participant_id)
        return None

    @staticmethod
    def _clean_task_title(title: Optional[str]) -> str:
        clean = re.sub(r"\s+", " ", str(title or "").strip())
        if clean.lower().startswith("task:"):
            clean = clean[5:].strip()
        return clean[:120].rstrip()

    @staticmethod
    def _normalize_task_key(title: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()

    @classmethod
    def _task_title_tokens(cls, title: str) -> set[str]:
        stop_words = {
            "a", "an", "the", "to", "by", "for", "and", "or", "of", "with",
            "will", "task", "short", "one", "another"
        }
        return {
            token
            for token in cls._normalize_task_key(title).split()
            if token and token not in stop_words
        }

    @classmethod
    def _task_titles_match(cls, left: str, right: str) -> bool:
        left_key = cls._normalize_task_key(left)
        right_key = cls._normalize_task_key(right)
        if left_key == right_key:
            return True

        left_tokens = cls._task_title_tokens(left)
        right_tokens = cls._task_title_tokens(right)
        if len(left_tokens) < 3 or len(right_tokens) < 3:
            return False

        overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
        return overlap >= 0.75

    @staticmethod
    def _normalize_frequency(frequency: Optional[str]) -> str:
        value = (frequency or "daily").strip().lower().replace("-", "_")
        if value in {"weekly", "week"}:
            return "weekly"
        if value in {"one_time", "one time", "once", "none"}:
            return "one_time"
        return "daily"

    @staticmethod
    def _looks_one_time_task(title: str, description: Optional[str]) -> bool:
        text = f"{title or ''} {description or ''}".lower()
        if any(marker in text for marker in ["every day", "daily", "every week", "weekly", "each week"]):
            return False
        if any(marker in text for marker in ["one-time", "one time", "once", "single"]):
            return True
        if re.search(r"\b(one|1)\b", text) and re.search(
            r"\b(this week|this sunday|this weekend|today|tomorrow)\b",
            text,
        ):
            return True
        return False

    @staticmethod
    def _normalize_duration_days(duration_days) -> int:
        try:
            return max(1, min(90, int(duration_days)))
        except (TypeError, ValueError):
            return 7

    def _find_duplicate_task(
        self,
        group_id: uuid.UUID,
        assigned_to: uuid.UUID,
        title: str,
        source_session_id: Optional[uuid.UUID] = None
    ) -> Optional[CheckIn]:
        if not self._normalize_task_key(title):
            return None

        candidates = self.db.query(CheckIn).filter(
            CheckIn.group_id == group_id,
            CheckIn.assigned_to == assigned_to
        ).all()
        for candidate in candidates:
            if not self._task_titles_match(candidate.title, title):
                continue
            if candidate.status in {'proposed', 'active', 'awaiting_verification', 'needs_work'}:
                return candidate
            if source_session_id and candidate.created_from_session == source_session_id:
                return candidate
        return None
