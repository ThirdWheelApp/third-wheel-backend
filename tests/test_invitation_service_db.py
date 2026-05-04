import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Group, Session, User
from app.services.invitation_service import (
    accept_pending_relationship_invite,
    create_or_reuse_pending_relationship,
)


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="requires TEST_DATABASE_URL pointing at an isolated PostgreSQL database",
)


@pytest.fixture()
def db_session():
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_pending_invite_acceptance_preserves_private_session_group(db_session):
    inviter = User(
        id=uuid.uuid4(),
        email="alex@example.com",
        name="Alex",
    )
    db_session.add(inviter)
    db_session.commit()

    pending_group = create_or_reuse_pending_relationship(
        db_session,
        inviter=inviter,
        invited_email="jordan@example.com",
        relationship_type="Married",
        relationship_description="Working on trust and communication.",
        is_long_distance=False,
    )
    db_session.commit()
    db_session.refresh(pending_group)

    assert pending_group.status == "pending"
    assert pending_group.partner1_id == inviter.id
    assert pending_group.partner2_id is None
    assert pending_group.partner2_email == "jordan@example.com"
    assert pending_group.relationship_type == "Married"
    assert pending_group.relationship_description == "Working on trust and communication."
    assert pending_group.is_long_distance is False

    private_session = Session(
        id=uuid.uuid4(),
        group_id=pending_group.id,
        type="private",
        status="active",
        created_by=inviter.id,
        participants=[inviter.id],
    )
    db_session.add(private_session)
    db_session.commit()

    invited = User(
        id=uuid.uuid4(),
        email="JORDAN@example.com",
        name="Jordan",
    )
    db_session.add(invited)
    db_session.commit()

    accepted_group = accept_pending_relationship_invite(
        db_session,
        invited_user=invited,
        invited_by=str(inviter.id),
        group_id=str(pending_group.id),
    )
    db_session.commit()
    db_session.refresh(accepted_group)

    assert accepted_group.id == pending_group.id
    assert accepted_group.status == "active"
    assert accepted_group.partner1_id == inviter.id
    assert accepted_group.partner2_id == invited.id

    persisted_session = db_session.query(Session).filter(Session.id == private_session.id).one()
    assert persisted_session.group_id == accepted_group.id

    inviter_groups = db_session.query(Group).filter(
        (Group.partner1_id == inviter.id) | (Group.partner2_id == inviter.id)
    ).all()
    invited_groups = db_session.query(Group).filter(
        (Group.partner1_id == invited.id) | (Group.partner2_id == invited.id)
    ).all()

    assert [group.id for group in inviter_groups] == [accepted_group.id]
    assert [group.id for group in invited_groups] == [accepted_group.id]


def test_no_email_invite_flow_through_api(db_session):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.utils.auth import get_current_user

    inviter_id = uuid.uuid4()
    partner_id = uuid.uuid4()

    client = TestClient(app)
    try:
        response = client.post(
            "/api/users/initialize",
            json={
                "supabaseUserId": str(inviter_id),
                "email": "alex-api@example.com",
                "name": "Alex API",
            },
        )
        assert response.status_code == 200, response.text

        invite_response = client.post(
            "/api/users/invite-partner",
            json={
                "partnerEmail": "jordan-api@example.com",
                "inviterName": "Alex API",
                "inviterEmail": "alex-api@example.com",
                "inviterUserId": str(inviter_id),
                "relationshipType": "Engaged",
                "relationshipDescription": "We want help with conflict repair.",
                "isLongDistance": True,
                "redirectTo": "http://localhost:8081",
            },
        )
        assert invite_response.status_code == 200, invite_response.text
        invite_payload = invite_response.json()
        assert invite_payload["success"] is True
        assert invite_payload["groupId"]
        assert "mode=invite" in invite_payload["inviteUrl"]
        assert f"invitedBy={inviter_id}" in invite_payload["inviteUrl"]
        assert f"groupId={invite_payload['groupId']}" in invite_payload["inviteUrl"]
        assert "relationshipType=Engaged" in invite_payload["inviteUrl"]
        assert "isLongDistance=true" in invite_payload["inviteUrl"]

        app.dependency_overrides[get_current_user] = lambda: str(inviter_id)
        inviter_groups = client.get("/api/groups/my-groups")
        assert inviter_groups.status_code == 200, inviter_groups.text
        assert inviter_groups.json()[0]["status"] == "pending"
        assert inviter_groups.json()[0]["partner2Id"] is None

        response = client.post(
            "/api/users/initialize",
            json={
                "supabaseUserId": str(partner_id),
                "email": "jordan-api@example.com",
                "name": "Jordan API",
            },
        )
        assert response.status_code == 200, response.text

        app.dependency_overrides[get_current_user] = lambda: str(partner_id)
        accept_response = client.post(
            "/api/groups/accept-invite",
            json={
                "invitedBy": str(inviter_id),
                "groupId": invite_payload["groupId"],
            },
        )
        assert accept_response.status_code == 200, accept_response.text
        accepted_group = accept_response.json()
        assert accepted_group["id"] == invite_payload["groupId"]
        assert accepted_group["status"] == "active"
        assert accepted_group["partner1Id"] == str(inviter_id)
        assert accepted_group["partner2Id"] == str(partner_id)
        assert accepted_group["relationshipType"] == "Engaged"
        assert accepted_group["relationshipDescription"] == "We want help with conflict repair."
        assert accepted_group["isLongDistance"] is True

        partner_groups = client.get("/api/groups/my-groups")
        assert partner_groups.status_code == 200, partner_groups.text
        assert partner_groups.json()[0]["id"] == invite_payload["groupId"]

        joint_response = client.post(
            "/api/sessions/",
            json={
                "groupId": invite_payload["groupId"],
                "sessionType": "joint",
                "participants": [str(inviter_id), str(partner_id)],
                "scheduledFor": "2030-01-01T00:00:00Z",
            },
        )
        assert joint_response.status_code == 200, joint_response.text
        joint_payload = joint_response.json()
        assert joint_payload["status"] == "scheduled"
        assert set(joint_payload["participants"]) == {str(inviter_id), str(partner_id)}

        app.dependency_overrides[get_current_user] = lambda: str(partner_id)
        join_response = client.post(f"/api/sessions/{joint_payload['id']}/join")
        assert join_response.status_code == 200, join_response.text
        joined_payload = join_response.json()
        assert joined_payload["status"] == "active"
        assert joined_payload["startedAt"]
    finally:
        app.dependency_overrides.clear()
        client.close()
