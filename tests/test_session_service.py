import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Group, Session, User
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
