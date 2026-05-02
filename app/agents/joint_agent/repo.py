"""
Joint Agent Repository

Data access for Joint Agent.
Simpler than Private Agent since Joint Agent mainly orchestrates.
"""

from sqlalchemy.orm import Session
from app.db.models import GroupContext, JointGuidanceContext
from app.config.settings import settings
from typing import List, Dict
import uuid


class JointAgentRepository:
    """
    Repository for Joint Agent data access.

    Joint Agent primarily orchestrates Private Agents,
    but can also access shared group context directly.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_group_context(self, group_id: str) -> List[Dict]:
        """
        Load shared group context.

        Args:
            group_id: UUID of the relationship/group

        Returns:
            List of group context dictionaries
        """
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)

        group_contexts = (
            self.db.query(GroupContext)
            .filter(GroupContext.group_id == group_id)
            .order_by(GroupContext.created_at.desc())
            .limit(settings.MAX_CONTEXTS_PER_LOAD)
            .all()
        )

        return [ctx.data for ctx in group_contexts if ctx.data]

    def get_joint_guidance_context(self, group_id: str) -> List[Dict]:
        """
        Load redacted private-informed guidance for joint sessions.

        These records are generated from private sessions but should contain
        only non-revealing therapist strategy, never raw private facts.
        """
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)

        guidance_contexts = (
            self.db.query(JointGuidanceContext)
            .filter(
                JointGuidanceContext.group_id == group_id,
                JointGuidanceContext.active == True  # noqa: E712
            )
            .order_by(JointGuidanceContext.created_at.desc())
            .limit(settings.MAX_CONTEXTS_PER_LOAD)
            .all()
        )

        return [ctx.data for ctx in guidance_contexts if ctx.data]
