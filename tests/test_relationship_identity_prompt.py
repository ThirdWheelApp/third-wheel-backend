import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.private_agent.agent import PrivateAgent
from app.agents.private_agent.repo import PrivateAgentRepository
from app.db.models import Base, Group, User


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


class CapturingMessagesClient:
    def __init__(self):
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(text="captured response")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=2),
        )


class CapturingClient:
    def __init__(self):
        self.messages = CapturingMessagesClient()


def test_private_prompt_includes_relationship_profile_and_unknown_pronoun_guard(db_session):
    user = User(id=uuid.uuid4(), email="alex-identity@example.com", name="Alex")
    partner = User(id=uuid.uuid4(), email="jordan-identity@example.com", name="Jordan")
    db_session.add_all([user, partner])
    db_session.commit()

    group = Group(
        id=uuid.uuid4(),
        partner1_id=user.id,
        partner2_id=partner.id,
        status="active",
    )
    db_session.add(group)
    db_session.commit()

    repo = PrivateAgentRepository(db_session)
    agent = PrivateAgent(str(user.id), str(group.id), repo)
    fake_client = CapturingClient()
    agent.client = fake_client

    response = asyncio.run(agent.get_private_message("I feel stuck.", []))

    assert response == "captured response"
    prompt = fake_client.messages.last_kwargs["messages"][0]["content"]
    assert "Relationship profile:" in prompt
    assert "Current user: Alex" in prompt
    assert "Partner: Jordan" in prompt
    assert "Partner pronouns/gender: Unknown" in prompt
    assert "Do not infer gendered pronouns" in prompt
    assert "use the partner's name or 'your partner' when unsure" in prompt


def test_private_prompt_surfaces_relationship_description_as_identity_source(db_session):
    user = User(id=uuid.uuid4(), email="sam-identity@example.com", name="Sam")
    partner = User(id=uuid.uuid4(), email="maya-identity@example.com", name="Maya")
    db_session.add_all([user, partner])
    db_session.commit()

    group = Group(
        id=uuid.uuid4(),
        partner1_id=user.id,
        partner2_id=partner.id,
        relationship_type="Dating",
        relationship_description="My partner Maya is a woman and uses she/her pronouns.",
        status="active",
    )
    db_session.add(group)
    db_session.commit()

    profile = PrivateAgentRepository(db_session).get_relationship_profile_text(
        str(user.id),
        str(group.id),
    )

    assert "Partner: Maya" in profile
    assert "Relationship type: Dating" in profile
    assert "Maya is a woman and uses she/her pronouns" in profile
