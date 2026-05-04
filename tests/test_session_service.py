import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Group, Message, Session, User
from app.services.context_service import ContextService
from app.services.session_service import SessionService


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


def test_end_session_can_return_before_relationship_processing(db_session, monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="alex-session@example.com",
        name="Alex Session",
    )
    db_session.add(user)
    db_session.commit()

    group = Group(
        id=uuid.uuid4(),
        partner1_id=user.id,
        partner2_email="partner-session@example.com",
        status="pending",
    )
    db_session.add(group)
    db_session.commit()

    private_session = Session(
        id=uuid.uuid4(),
        group_id=group.id,
        type="private",
        status="active",
        created_by=user.id,
        participants=[user.id],
    )
    db_session.add(private_session)
    db_session.commit()

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("context extraction should not run in the fast end path")

    monkeypatch.setattr(ContextService, "extract_context_from_session", fail_if_called)

    result = asyncio.run(
        SessionService(db_session).end_session(
            str(private_session.id),
            str(user.id),
            process_post_session=False,
        )
    )

    db_session.refresh(private_session)
    assert result["status"] == "ending"
    assert result["post_processing_required"] is True
    assert private_session.status == "ending"
    assert private_session.ended_at is not None


def test_process_ended_private_session_concludes_after_extraction(db_session, monkeypatch):
    user = User(
        id=uuid.uuid4(),
        email="alex-process@example.com",
        name="Alex Process",
    )
    db_session.add(user)
    db_session.commit()

    group = Group(
        id=uuid.uuid4(),
        partner1_id=user.id,
        partner2_email="partner-process@example.com",
        status="pending",
    )
    db_session.add(group)
    db_session.commit()

    private_session = Session(
        id=uuid.uuid4(),
        group_id=group.id,
        type="private",
        status="ending",
        created_by=user.id,
        participants=[user.id],
    )
    db_session.add(private_session)
    db_session.commit()

    async def empty_extraction(*args, **kwargs):
        return {
            "user_a_contexts": [],
            "user_b_contexts": [],
            "group_contexts": [],
            "check_ins": [],
        }

    monkeypatch.setattr(ContextService, "extract_context_from_session", empty_extraction)

    result = asyncio.run(
        SessionService(db_session).process_ended_session(str(private_session.id))
    )

    db_session.refresh(private_session)
    assert result["status"] == "concluded"
    assert result["post_processing_required"] is False
    assert private_session.status == "concluded"


def test_private_session_contexts_default_to_non_shareable(db_session):
    user = User(
        id=uuid.uuid4(),
        email="alex-private-boundary@example.com",
        name="Alex Private Boundary",
    )
    db_session.add(user)
    db_session.commit()

    group = Group(
        id=uuid.uuid4(),
        partner1_id=user.id,
        partner2_email="partner-private-boundary@example.com",
        status="pending",
    )
    db_session.add(group)
    db_session.commit()

    private_session = Session(
        id=uuid.uuid4(),
        group_id=group.id,
        type="private",
        status="ending",
        created_by=user.id,
        participants=[user.id],
    )

    extracted = {
        "user_a_contexts": [
            {"text": "Benign but learned only in private", "secret_level": 0},
        ],
        "user_b_contexts": [
            {"text": "Misassigned private context", "secret_level": 0},
        ],
        "group_contexts": [
            {"text": "Should not be persisted from private session"},
        ],
        "check_ins": [
            {"title": "Should not become a joint task"},
        ],
    }

    normalized = ContextService(db_session)._normalize_extracted_data_for_session(
        session=private_session,
        extracted_data=extracted,
    )

    assert normalized["user_b_contexts"] == []
    assert normalized["group_contexts"] == []
    assert normalized["check_ins"] == []
    assert len(normalized["user_a_contexts"]) == 2
    assert all(ctx["secret_level"] >= 1 for ctx in normalized["user_a_contexts"])


def test_private_session_extracts_clear_partner_pronoun_identity(db_session):
    user = User(
        id=uuid.uuid4(),
        email="alex-pronouns@example.com",
        name="Alex Pronouns",
    )
    db_session.add(user)
    db_session.commit()

    private_session = Session(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        type="private",
        status="ending",
        created_by=user.id,
        participants=[user.id],
    )

    messages = [
        Message(
            id=uuid.uuid4(),
            session_id=private_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="My partner hurt me.",
            sequence_number=1,
        ),
        Message(
            id=uuid.uuid4(),
            session_id=private_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="She usually gets angry when I bring it up.",
            sequence_number=2,
        ),
        Message(
            id=uuid.uuid4(),
            session_id=private_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="She might punish me again.",
            sequence_number=3,
        ),
    ]

    extracted = {"user_a_contexts": [], "user_b_contexts": []}
    ContextService(db_session)._add_deterministic_identity_contexts(
        session=private_session,
        extracted_data=extracted,
        messages=messages,
    )

    assert extracted["user_a_contexts"] == [
        {
            "text": "User refers to partner with she/her pronouns.",
            "secret_level": 1,
            "tags": ["identity", "partner-pronouns"],
            "category": "identity",
        }
    ]


def test_private_session_skips_ambiguous_partner_pronoun_identity(db_session):
    user = User(
        id=uuid.uuid4(),
        email="alex-ambiguous@example.com",
        name="Alex Ambiguous",
    )

    private_session = Session(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        type="private",
        status="ending",
        created_by=user.id,
        participants=[user.id],
    )

    messages = [
        Message(
            id=uuid.uuid4(),
            session_id=private_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="My partner and my friend are both involved.",
            sequence_number=1,
        ),
        Message(
            id=uuid.uuid4(),
            session_id=private_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="She said one thing and he said another.",
            sequence_number=2,
        ),
    ]

    extracted = {"user_a_contexts": [], "user_b_contexts": []}
    ContextService(db_session)._add_deterministic_identity_contexts(
        session=private_session,
        extracted_data=extracted,
        messages=messages,
    )

    assert extracted["user_a_contexts"] == []


def test_joint_session_skips_deterministic_partner_pronoun_identity(db_session):
    user = User(
        id=uuid.uuid4(),
        email="alex-joint-pronouns@example.com",
        name="Alex Joint Pronouns",
    )
    partner = User(
        id=uuid.uuid4(),
        email="jordan-joint-pronouns@example.com",
        name="Jordan Joint Pronouns",
    )

    joint_session = Session(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        type="joint",
        status="ending",
        created_by=user.id,
        participants=[user.id, partner.id],
    )

    messages = [
        Message(
            id=uuid.uuid4(),
            session_id=joint_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="My partner is worried.",
            sequence_number=1,
        ),
        Message(
            id=uuid.uuid4(),
            session_id=joint_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="She wants to slow down.",
            sequence_number=2,
        ),
        Message(
            id=uuid.uuid4(),
            session_id=joint_session.id,
            sender_id=str(user.id),
            sender_name=user.name,
            content="She said this feels hard.",
            sequence_number=3,
        ),
    ]

    extracted = {"user_a_contexts": [], "user_b_contexts": []}
    ContextService(db_session)._add_deterministic_identity_contexts(
        session=joint_session,
        extracted_data=extracted,
        messages=messages,
    )

    assert extracted["user_a_contexts"] == []
