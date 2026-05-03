"""
Relationship invitation helpers.

The backend owns pending relationship state so invite acceptance does not
depend on a client remembering to create a group at the right moment.
"""

from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy.orm import Session

from app.db.models import Group, User


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _apply_relationship_details(
    group: Group,
    *,
    relationship_type: Optional[str] = None,
    relationship_description: Optional[str] = None,
    is_long_distance: Optional[bool] = None,
) -> None:
    normalized_type = _normalize_optional_text(relationship_type)
    normalized_description = _normalize_optional_text(relationship_description)

    if normalized_type is not None:
        group.relationship_type = normalized_type
    if normalized_description is not None:
        group.relationship_description = normalized_description
    if is_long_distance is not None:
        group.is_long_distance = is_long_distance


def create_or_reuse_pending_relationship(
    db: Session,
    *,
    inviter: User,
    invited_email: str,
    relationship_type: Optional[str] = None,
    relationship_description: Optional[str] = None,
    is_long_distance: Optional[bool] = None,
) -> Group:
    """
    Create or reuse a relationship shell for a partner invite.

    If the invited partner already accepted, the active group is returned.
    Otherwise a pending group owned by the inviter is returned.
    """
    normalized_email = normalize_email(invited_email)

    invited_user = db.query(User).filter(User.email == normalized_email).first()
    if invited_user:
        existing_active = db.query(Group).filter(
            (
                (Group.partner1_id == inviter.id)
                & (Group.partner2_id == invited_user.id)
            )
            | (
                (Group.partner1_id == invited_user.id)
                & (Group.partner2_id == inviter.id)
            )
        ).first()
        if existing_active:
            _apply_relationship_details(
                existing_active,
                relationship_type=relationship_type,
                relationship_description=relationship_description,
                is_long_distance=is_long_distance,
            )
            return existing_active

    pending_group = db.query(Group).filter(
        Group.partner1_id == inviter.id,
        Group.partner2_email == normalized_email,
        Group.status == "pending",
    ).first()

    if pending_group:
        _apply_relationship_details(
            pending_group,
            relationship_type=relationship_type,
            relationship_description=relationship_description,
            is_long_distance=is_long_distance,
        )
        return pending_group

    group = Group(
        id=uuid.uuid4(),
        partner1_id=inviter.id,
        partner2_id=None,
        partner2_email=normalized_email,
        invite_token=uuid.uuid4().hex,
        relationship_type=_normalize_optional_text(relationship_type),
        relationship_description=_normalize_optional_text(relationship_description),
        is_long_distance=is_long_distance,
        status="pending",
    )
    db.add(group)
    db.flush()
    return group


def accept_pending_relationship_invite(
    db: Session,
    *,
    invited_user: User,
    invited_by: Optional[str] = None,
    group_id: Optional[str] = None,
) -> Group:
    """
    Activate a pending relationship for an invited user.

    The signed-in user's email must match the pending invite email. This keeps
    acceptance tied to the intended recipient without depending on email
    delivery during local tests.
    """
    invited_email = normalize_email(invited_user.email)
    query = db.query(Group).filter(
        Group.partner2_email == invited_email,
        Group.status == "pending",
    )

    if group_id:
        query = query.filter(Group.id == uuid.UUID(group_id))
    if invited_by:
        query = query.filter(Group.partner1_id == uuid.UUID(invited_by))

    group = query.order_by(Group.created_at.desc()).first()

    if not group:
        existing_query = db.query(Group).filter(
            (
                (Group.partner1_id == invited_user.id)
                | (Group.partner2_id == invited_user.id)
            ),
            Group.status == "active",
        )
        if invited_by:
            inviter_uuid = uuid.UUID(invited_by)
            existing_query = existing_query.filter(
                (Group.partner1_id == inviter_uuid)
                | (Group.partner2_id == inviter_uuid)
            )
        if group_id:
            existing_query = existing_query.filter(Group.id == uuid.UUID(group_id))

        existing = existing_query.order_by(Group.created_at.desc()).first()
        if existing:
            return existing
        raise ValueError("No pending invitation found for this account")

    if group.partner1_id == invited_user.id:
        raise ValueError("Inviter cannot accept their own invitation")

    existing_active = db.query(Group).filter(
        (
            (Group.partner1_id == group.partner1_id)
            & (Group.partner2_id == invited_user.id)
        )
        | (
            (Group.partner1_id == invited_user.id)
            & (Group.partner2_id == group.partner1_id)
        )
    ).first()
    if existing_active:
        group.status = "inactive"
        db.flush()
        return existing_active

    group.partner2_id = invited_user.id
    group.partner2_email = invited_email
    group.status = "active"
    db.flush()
    return group
