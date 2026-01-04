#!/usr/bin/env python3
"""
Seed Demo Data Script

Creates sample data for demo and testing purposes:
- 2 test users (Alex and Jordan)
- 1 relationship group
- Sample sessions with messages
- Sample check-ins
- Sample contexts

Usage:
    python scripts/seed_demo_data.py

Environment:
    DATABASE_URL must be set in .env
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal, engine, Base
from app.db.models import User, Group, Session, Message, CheckIn, PrivateUserContext, GroupContext
from datetime import datetime, timedelta
import uuid


# Demo UUIDs (consistent for testing)
DEMO_USER_A_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
DEMO_USER_B_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
DEMO_GROUP_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
DEMO_SESSION_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


def create_demo_users(db):
    """Create demo users Alex and Jordan."""
    print("Creating demo users...")

    # Check if users already exist
    existing_a = db.query(User).filter(User.id == DEMO_USER_A_ID).first()
    existing_b = db.query(User).filter(User.id == DEMO_USER_B_ID).first()

    if not existing_a:
        user_a = User(
            id=DEMO_USER_A_ID,
            name="Alex Demo",
            email="alex@demo.thirdwheel.app",
            created_at=datetime.utcnow()
        )
        db.add(user_a)
        print(f"  Created user: Alex Demo ({DEMO_USER_A_ID})")
    else:
        print(f"  User Alex already exists")

    if not existing_b:
        user_b = User(
            id=DEMO_USER_B_ID,
            name="Jordan Demo",
            email="jordan@demo.thirdwheel.app",
            created_at=datetime.utcnow()
        )
        db.add(user_b)
        print(f"  Created user: Jordan Demo ({DEMO_USER_B_ID})")
    else:
        print(f"  User Jordan already exists")

    db.commit()
    return DEMO_USER_A_ID, DEMO_USER_B_ID


def create_demo_group(db, user_a_id, user_b_id):
    """Create demo relationship group."""
    print("Creating demo group...")

    existing = db.query(Group).filter(Group.id == DEMO_GROUP_ID).first()

    if not existing:
        group = Group(
            id=DEMO_GROUP_ID,
            name="Alex & Jordan",
            partner1_id=user_a_id,
            partner2_id=user_b_id,
            created_at=datetime.utcnow()
        )
        db.add(group)
        db.commit()
        print(f"  Created group: Alex & Jordan ({DEMO_GROUP_ID})")
    else:
        print(f"  Group already exists")

    return DEMO_GROUP_ID


def create_demo_session(db, group_id, user_a_id, user_b_id):
    """Create demo joint therapy session with sample messages."""
    print("Creating demo session...")

    existing = db.query(Session).filter(Session.id == DEMO_SESSION_ID).first()

    if not existing:
        session = Session(
            id=DEMO_SESSION_ID,
            group_id=group_id,
            type="joint",
            status="active",
            participants=[user_a_id, user_b_id],
            created_at=datetime.utcnow(),
            current_context={}
        )
        db.add(session)
        db.commit()
        print(f"  Created session ({DEMO_SESSION_ID})")

        # Add sample messages
        messages = [
            {
                "sender_id": str(user_a_id),
                "sender_name": "Alex Demo",
                "content": "Hi, we're here to work on our communication."
            },
            {
                "sender_id": str(user_b_id),
                "sender_name": "Jordan Demo",
                "content": "Yes, we've been having some issues lately."
            },
            {
                "sender_id": "therapist",
                "sender_name": "AI Therapist",
                "content": "Welcome, Alex and Jordan. I'm glad you're both here to work on this together. Communication is the foundation of any healthy relationship. Can you tell me more about what's been happening?"
            },
            {
                "sender_id": str(user_a_id),
                "sender_name": "Alex Demo",
                "content": "I feel like Jordan doesn't listen when I try to share my concerns."
            },
            {
                "sender_id": "therapist",
                "sender_name": "AI Therapist",
                "content": "Alex, thank you for sharing that. Feeling unheard can be really frustrating. Jordan, how does it feel to hear Alex say that?"
            }
        ]

        for i, msg_data in enumerate(messages, 1):
            msg = Message(
                id=uuid.uuid4(),
                session_id=DEMO_SESSION_ID,
                sender_id=msg_data["sender_id"],
                sender_name=msg_data["sender_name"],
                content=msg_data["content"],
                sequence_number=i,
                timestamp=datetime.utcnow() + timedelta(seconds=i * 30)
            )
            db.add(msg)

        db.commit()
        print(f"  Added {len(messages)} sample messages")
    else:
        print(f"  Session already exists")

    return DEMO_SESSION_ID


def create_demo_checkins(db, group_id, user_a_id, user_b_id):
    """Create sample check-ins."""
    print("Creating demo check-ins...")

    existing_count = db.query(CheckIn).filter(CheckIn.group_id == group_id).count()

    if existing_count == 0:
        checkins = [
            {
                "title": "Daily Appreciation",
                "description": "Share one thing you appreciate about your partner today",
                "assigned_to": user_a_id,
                "verifier_id": user_b_id,
                "status": "active",
                "requires_verification": True,
                "frequency": "daily",
                "progress": {"completed": 3, "total": 7}
            },
            {
                "title": "Active Listening Practice",
                "description": "Practice reflective listening during one conversation today",
                "assigned_to": user_b_id,
                "verifier_id": user_a_id,
                "status": "awaiting_verification",
                "requires_verification": True,
                "frequency": "daily",
                "progress": {"completed": 5, "total": 7}
            },
            {
                "title": "Weekly Date Night",
                "description": "Plan and have an uninterrupted date night together",
                "assigned_to": user_a_id,
                "verifier_id": None,
                "status": "active",
                "requires_verification": False,
                "frequency": "weekly",
                "progress": {"completed": 2, "total": 4}
            }
        ]

        for checkin_data in checkins:
            checkin = CheckIn(
                id=uuid.uuid4(),
                group_id=group_id,
                title=checkin_data["title"],
                description=checkin_data["description"],
                assigned_to=checkin_data["assigned_to"],
                verifier_id=checkin_data.get("verifier_id"),
                status=checkin_data["status"],
                requires_verification=checkin_data["requires_verification"],
                frequency=checkin_data.get("frequency"),
                progress=checkin_data.get("progress"),
                created_at=datetime.utcnow()
            )
            db.add(checkin)

        db.commit()
        print(f"  Created {len(checkins)} check-ins")
    else:
        print(f"  Check-ins already exist ({existing_count})")


def create_demo_contexts(db, group_id, user_a_id, user_b_id):
    """Create sample private and group contexts."""
    print("Creating demo contexts...")

    # Private contexts for User A
    private_contexts_a = [
        {
            "text": "Feels unheard during important conversations",
            "secret_level": 3,
            "tags": ["communication", "listening"],
            "category": "communication"
        },
        {
            "text": "Values quality time and verbal affirmation",
            "secret_level": 2,
            "tags": ["love-language", "needs"],
            "category": "preferences"
        }
    ]

    # Private contexts for User B
    private_contexts_b = [
        {
            "text": "Tends to problem-solve instead of just listening",
            "secret_level": 3,
            "tags": ["communication", "patterns"],
            "category": "communication"
        },
        {
            "text": "Expresses love through acts of service",
            "secret_level": 2,
            "tags": ["love-language", "expression"],
            "category": "preferences"
        }
    ]

    # Group contexts
    group_contexts = [
        {
            "text": "Both partners are committed to improving communication",
            "tags": ["commitment", "communication"],
            "participants": [str(user_a_id), str(user_b_id)]
        },
        {
            "text": "Established weekly check-in routine",
            "tags": ["routine", "check-ins"],
            "participants": [str(user_a_id), str(user_b_id)]
        }
    ]

    # Check if contexts already exist
    existing_private = db.query(PrivateUserContext).filter(
        PrivateUserContext.group_id == group_id
    ).count()

    if existing_private == 0:
        for ctx_data in private_contexts_a:
            ctx = PrivateUserContext(
                user_id=user_a_id,
                group_id=group_id,
                context_id=uuid.uuid4(),
                data={
                    **ctx_data,
                    "created_at": datetime.utcnow().isoformat(),
                    "source": "demo_seed"
                },
                created_at=datetime.utcnow()
            )
            db.add(ctx)

        for ctx_data in private_contexts_b:
            ctx = PrivateUserContext(
                user_id=user_b_id,
                group_id=group_id,
                context_id=uuid.uuid4(),
                data={
                    **ctx_data,
                    "created_at": datetime.utcnow().isoformat(),
                    "source": "demo_seed"
                },
                created_at=datetime.utcnow()
            )
            db.add(ctx)

        for ctx_data in group_contexts:
            ctx = GroupContext(
                group_id=group_id,
                context_id=uuid.uuid4(),
                data={
                    **ctx_data,
                    "created_at": datetime.utcnow().isoformat(),
                    "source": "demo_seed"
                },
                created_at=datetime.utcnow()
            )
            db.add(ctx)

        db.commit()
        print(f"  Created {len(private_contexts_a) + len(private_contexts_b)} private contexts")
        print(f"  Created {len(group_contexts)} group contexts")
    else:
        print(f"  Contexts already exist")


def main():
    """Main entry point for seeding demo data."""
    print("=" * 50)
    print("Third Wheel Demo Data Seeder")
    print("=" * 50)
    print()

    # Create tables if they don't exist
    print("Ensuring database tables exist...")
    Base.metadata.create_all(bind=engine)
    print()

    # Create session
    db = SessionLocal()

    try:
        # Create demo data
        user_a_id, user_b_id = create_demo_users(db)
        group_id = create_demo_group(db, user_a_id, user_b_id)
        session_id = create_demo_session(db, group_id, user_a_id, user_b_id)
        create_demo_checkins(db, group_id, user_a_id, user_b_id)
        create_demo_contexts(db, group_id, user_a_id, user_b_id)

        print()
        print("=" * 50)
        print("Demo data seeded successfully!")
        print("=" * 50)
        print()
        print("Demo Credentials:")
        print(f"  User A (Alex):  ID={DEMO_USER_A_ID}")
        print(f"  User B (Jordan): ID={DEMO_USER_B_ID}")
        print(f"  Group:          ID={DEMO_GROUP_ID}")
        print(f"  Session:        ID={DEMO_SESSION_ID}")
        print()
        print("To use demo mode, set DEMO_MODE=true in your .env file")
        print()

    except Exception as e:
        print(f"Error seeding data: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
