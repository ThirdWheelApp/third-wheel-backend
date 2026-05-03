import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, CheckIn, Group, Session, User
from app.services.checkin_service import CheckInService


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


@pytest.fixture()
def couple_session(db_session):
    alex = User(id=uuid.uuid4(), email="alex-tasks@example.com", name="Alex")
    jordan = User(id=uuid.uuid4(), email="jordan-tasks@example.com", name="Jordan")
    db_session.add_all([alex, jordan])
    db_session.commit()

    group = Group(
        id=uuid.uuid4(),
        partner1_id=alex.id,
        partner2_id=jordan.id,
        status="active",
    )
    db_session.add(group)
    db_session.commit()

    session = Session(
        id=uuid.uuid4(),
        group_id=group.id,
        type="joint",
        status="active",
        created_by=alex.id,
        participants=[alex.id, jordan.id],
    )
    db_session.add(session)
    db_session.commit()

    return alex, jordan, group, session


def test_user_proposed_self_task_requires_partner_acceptance(db_session, couple_session):
    alex, jordan, group, session = couple_session
    service = CheckInService(db_session)

    task = service.create_task_proposal(
        group_id=str(group.id),
        assigned_to=str(alex.id),
        verifier_id=str(jordan.id),
        proposed_by=str(alex.id),
        title="Send a daily check-in text",
        description="Alex proposed sending a daily check-in text after work.",
        frequency="daily",
        duration_days=1,
        session_id=str(session.id),
    )

    assert task.status == "proposed"
    assert task.assigned_approved is True
    assert task.verifier_approved is False

    result = asyncio.run(
        service.decide_task(
            checkin_id=str(task.id),
            user_id=str(jordan.id),
            decision="accepted",
        )
    )

    db_session.refresh(task)
    assert result["status"] == "active"
    assert task.status == "active"
    assert task.assigned_approved is True
    assert task.verifier_approved is True

    complete = asyncio.run(service.mark_checkin_done(str(task.id), str(alex.id)))
    db_session.refresh(task)
    assert complete["status"] == "completed"
    assert complete["progress"]["completed"] == 1
    assert task.status == "completed"
    assert task.progress["completed"] == 1


def test_partner_assigned_task_waits_for_assignee(db_session, couple_session):
    alex, jordan, group, session = couple_session
    service = CheckInService(db_session)

    task = service.create_task_proposal(
        group_id=str(group.id),
        assigned_to=str(jordan.id),
        verifier_id=str(alex.id),
        proposed_by=str(alex.id),
        title="Plan the Sunday budget conversation",
        description="Alex proposed that Jordan plan a concrete time to discuss the budget.",
        frequency="one_time",
        duration_days=1,
        session_id=str(session.id),
    )

    assert task.status == "proposed"
    assert task.assigned_approved is False
    assert task.verifier_approved is True

    with pytest.raises(ValueError, match="not currently active"):
        asyncio.run(service.mark_checkin_done(str(task.id), str(jordan.id)))

    result = asyncio.run(
        service.decide_task(
            checkin_id=str(task.id),
            user_id=str(jordan.id),
            decision="rejected",
            reason="Not the right task",
        )
    )

    db_session.refresh(task)
    assert result["status"] == "rejected"
    assert task.status == "rejected"
    assert task.verification_feedback == "Not the right task"


def test_session_extracted_task_concludes_pending_actions_after_both_decide(
    db_session,
    couple_session,
):
    alex, jordan, group, session = couple_session
    session.status = "pending_actions"
    db_session.commit()

    service = CheckInService(db_session)
    created = asyncio.run(
        service.create_checkins_from_extraction(
            session_id=str(session.id),
            group_id=str(group.id),
            checkins_data=[
                {
                    "title": "Use a repair phrase during conflict",
                    "description": "Pause and name one repair phrase when conflict escalates.",
                    "assigned_to": "user_a",
                    "frequency": "daily",
                    "duration_days": 7,
                    "requires_verification": True,
                }
            ],
        )
    )

    assert len(created) == 1
    task = created[0]
    assert task.status == "proposed"
    assert task.assigned_approved is False
    assert task.verifier_approved is False
    assert task.verifier_id == jordan.id

    first = asyncio.run(
        service.decide_task(str(task.id), str(alex.id), "accepted")
    )
    db_session.refresh(session)
    db_session.refresh(task)
    assert first["status"] == "proposed"
    assert session.status == "pending_actions"

    second = asyncio.run(
        service.decide_task(str(task.id), str(jordan.id), "accepted")
    )
    db_session.refresh(session)
    db_session.refresh(task)
    assert second["status"] == "active"
    assert task.status == "active"
    assert session.status == "concluded"

    done = asyncio.run(service.mark_checkin_done(str(task.id), str(alex.id)))
    db_session.refresh(task)
    assert done["status"] == "awaiting_verification"
    assert done["progress"]["completed"] == 1
    assert task.status == "awaiting_verification"

    verified = asyncio.run(service.verify_checkin(str(task.id), str(jordan.id), "verified"))
    db_session.refresh(task)
    assert verified["status"] == "active"
    assert task.status == "active"


def test_duplicate_open_task_proposals_are_ignored(db_session, couple_session):
    alex, jordan, group, session = couple_session
    service = CheckInService(db_session)

    first = service.create_task_proposal(
        group_id=str(group.id),
        assigned_to=str(alex.id),
        verifier_id=str(jordan.id),
        proposed_by=str(alex.id),
        title="Send a daily check-in text",
        session_id=str(session.id),
    )
    second = service.create_task_proposal(
        group_id=str(group.id),
        assigned_to=str(alex.id),
        verifier_id=str(jordan.id),
        proposed_by=str(alex.id),
        title="Task: send a daily check in text",
        session_id=str(session.id),
    )

    assert first is not None
    assert second is None
    assert db_session.query(CheckIn).count() == 1

    first.status = "completed"
    db_session.commit()
    fuzzy_duplicate = service.create_task_proposal(
        group_id=str(group.id),
        assigned_to=str(alex.id),
        verifier_id=str(jordan.id),
        proposed_by=str(jordan.id),
        title="Send daily check-in text",
        session_id=str(session.id),
    )

    assert fuzzy_duplicate is None
    assert db_session.query(CheckIn).count() == 1
