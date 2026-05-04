"""
Joint Agent Repository

Data access for Joint Agent.
Simpler than Private Agent since Joint Agent mainly orchestrates.
"""

from sqlalchemy.orm import Session
from app.db.models import Group, GroupContext, JointGuidanceContext, User
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

    def get_relationship_profile_text(self, group_id: str) -> str:
        """Return stable shared relationship identity facts for joint prompts."""
        group_uuid = uuid.UUID(group_id) if isinstance(group_id, str) else group_id
        group = self.db.query(Group).filter(Group.id == group_uuid).first()
        if not group:
            return (
                "Relationship profile unavailable. Use only names and pronouns "
                "explicitly stated in the live transcript; do not infer gendered pronouns."
            )

        partner_names = []
        for partner_id in [group.partner1_id, group.partner2_id]:
            if partner_id:
                user = self.db.query(User).filter(User.id == partner_id).first()
                if user:
                    partner_names.append(user.name)

        relationship_type = group.relationship_type or "not specified"
        relationship_description = group.relationship_description or "not specified"

        return "\n".join([
            f"Partners: {', '.join(partner_names) if partner_names else 'not specified'}",
            f"Relationship type: {relationship_type}",
            f"Relationship description: {relationship_description}",
            (
                "Pronoun/gender rule: Use explicitly stated pronouns/gender consistently. "
                "If unknown or ambiguous, use names or 'your partner'; do not infer gendered "
                "pronouns from names, relationship type, sexual roles, abuse dynamics, or stereotypes."
            ),
        ])
