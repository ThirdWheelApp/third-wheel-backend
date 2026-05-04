"""
Private Agent Repository

Data access layer for Private Agent.
Loads context from database for a specific user within a relationship.
"""

from sqlalchemy.orm import Session
from app.db.models import PrivateUserContext, GroupContext, Group, User
from app.config.settings import settings
from typing import List, Dict, Optional
import uuid


class PrivateAgentRepository:
    """
    Repository pattern for Private Agent data access.

    Responsibilities:
    - Load private user context for a specific user/group
    - Load shared group context
    - Filter contexts by secret level
    - Provide context in format suitable for LLM
    """

    def __init__(self, db: Session):
        self.db = db

    def get_all_context(self, user_id: str, group_id: Optional[str]) -> List[Dict]:
        """
        Load all context for a user within a specific relationship.

        Loads both:
        - Private user context (user-specific insights)
        - Group context (shared relationship patterns)

        Returns most recent MAX_CONTEXTS_PER_LOAD contexts.

        Args:
            user_id: UUID of the user
            group_id: UUID of the relationship/group (None for solo private sessions)

        Returns:
            List of context dictionaries sorted by recency

        Note:
            TODO: Replace with vector search when context grows large.
            For MVP, loading last 50 contexts works fine.
        """
        # Convert string UUIDs to UUID objects if needed
        if isinstance(user_id, str):
            user_id = uuid.UUID(user_id)

        # For solo private sessions without a relationship, return empty context
        # The user hasn't connected with a partner yet
        if group_id is None:
            return []

        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)

        # Load private context for this user in this relationship
        private_contexts = (
            self.db.query(PrivateUserContext)
            .filter(
                PrivateUserContext.user_id == user_id,
                PrivateUserContext.group_id == group_id
            )
            .order_by(PrivateUserContext.created_at.desc())
            .limit(settings.MAX_CONTEXTS_PER_LOAD)
            .all()
        )

        # Load shared group context
        group_contexts = (
            self.db.query(GroupContext)
            .filter(GroupContext.group_id == group_id)
            .order_by(GroupContext.created_at.desc())
            .limit(settings.MAX_CONTEXTS_PER_LOAD)
            .all()
        )

        # Combine and extract data
        all_contexts = []

        for ctx in private_contexts:
            data = ctx.data.copy() if ctx.data else {}
            data['context_type'] = 'private'
            all_contexts.append(data)

        for ctx in group_contexts:
            data = ctx.data.copy() if ctx.data else {}
            data['context_type'] = 'group'
            all_contexts.append(data)

        # Sort by created_at (most recent first)
        all_contexts.sort(
            key=lambda x: x.get('created_at', ''),
            reverse=True
        )

        return all_contexts

    def filter_by_secret_level(
        self,
        contexts: List[Dict],
        max_level: int
    ) -> List[Dict]:
        """
        Filter contexts to only include those with secret_level <= max_level.

        Used when sharing context in group sessions - only share
        contexts that are not too sensitive.

        Args:
            contexts: List of context dictionaries
            max_level: Maximum secret level to include (0-10)

        Returns:
            Filtered list of contexts

        Example:
            # Only share contexts with secret_level <= 5 in joint sessions
            shareable = repo.filter_by_secret_level(contexts, 5)
        """
        return [
            ctx for ctx in contexts
            if ctx.get('secret_level', 0) <= max_level
        ]

    def get_relationship_profile_text(
        self,
        user_id: str,
        group_id: Optional[str]
    ) -> str:
        """
        Return stable relationship identity facts for prompt continuity.

        Pronouns/gender are not first-class columns yet. Until they are, the
        prompt must explicitly avoid guessing when the stored relationship
        profile does not contain that information.
        """
        if group_id is None:
            current_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            current_user = self.db.query(User).filter(User.id == current_uuid).first()
            current_name = current_user.name if current_user else "unknown"
            return (
                f"Current user: {current_name}\n"
                "Partner: not connected yet\n"
                "Partner pronouns/gender: Unknown. Do not infer gendered pronouns; "
                "use 'your partner' unless the transcript or stored context explicitly states pronouns."
            )

        current_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        group_uuid = uuid.UUID(group_id) if isinstance(group_id, str) else group_id

        group = self.db.query(Group).filter(Group.id == group_uuid).first()
        current_user = self.db.query(User).filter(User.id == current_uuid).first()

        partner = None
        if group:
            partner_id = None
            if group.partner1_id == current_uuid:
                partner_id = group.partner2_id
            elif group.partner2_id == current_uuid:
                partner_id = group.partner1_id

            if partner_id:
                partner = self.db.query(User).filter(User.id == partner_id).first()

        current_name = current_user.name if current_user else "unknown"
        partner_name = partner.name if partner else "not connected yet"
        relationship_type = group.relationship_type if group and group.relationship_type else "not specified"
        relationship_description = (
            group.relationship_description
            if group and group.relationship_description
            else "not specified"
        )

        return "\n".join([
            f"Current user: {current_name}",
            f"Partner: {partner_name}",
            f"Relationship type: {relationship_type}",
            f"Relationship description: {relationship_description}",
            (
                "Partner pronouns/gender: Unknown unless explicitly stated in the relationship "
                "description, recent transcript, or stored context. Do not infer gendered pronouns "
                "from names, relationship type, sexual roles, abuse dynamics, or stereotypes; use "
                "the partner's name or 'your partner' when unsure."
            ),
        ])

    def get_recent_context(
        self,
        user_id: str,
        group_id: Optional[str],
        limit: int = 20
    ) -> List[Dict]:
        """
        Get most recent N contexts for a user.

        Useful for quick context loading without the full set.

        Args:
            user_id: UUID of the user
            group_id: UUID of the relationship (None for solo sessions)
            limit: Number of contexts to retrieve

        Returns:
            List of most recent contexts
        """
        contexts = self.get_all_context(user_id, group_id)
        return contexts[:limit]
