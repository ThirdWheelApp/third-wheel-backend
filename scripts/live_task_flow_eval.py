"""
Live task-flow eval for joint-session task proposals.

This script intentionally uses the real configured LLM client. Point
DATABASE_URL at a disposable PostgreSQL database before running; it drops and
recreates all app tables.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import settings
from app.db.database import Base, SessionLocal, engine
from app.db.models import CheckIn, Group, User
from app.services.chat_service import ChatService
from app.services.checkin_service import CheckInService
from app.services.session_service import SessionService


def compact(text: str | None, limit: int = 360) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[:limit] + "..."


async def seed_couple(db):
    alex_id = uuid.uuid4()
    jordan_id = uuid.uuid4()
    group_id = uuid.uuid4()
    alex = User(id=alex_id, email="alex.task.eval@example.com", name="Alex Task Eval")
    jordan = User(id=jordan_id, email="jordan.task.eval@example.com", name="Jordan Task Eval")
    db.add_all([alex, jordan])
    db.commit()

    group = Group(
        id=group_id,
        partner1_id=alex_id,
        partner2_id=jordan_id,
        partner2_email=jordan.email,
        relationship_type="dating",
        status="active",
        created_at=datetime.utcnow(),
    )
    db.add(group)
    db.commit()
    return alex_id, jordan_id, group_id


def print_tasks(db, label: str):
    tasks = db.query(CheckIn).order_by(CheckIn.created_at.asc()).all()
    for task in tasks:
        print(
            f"{label}_TASK={task.title}|status={task.status}|assigned={task.assigned_to}|"
            f"verifier={task.verifier_id}|assignedApproved={task.assigned_approved}|"
            f"verifierApproved={task.verifier_approved}|requiresVerification={task.requires_verification}|"
            f"progress={task.progress}"
        )
    return tasks


async def main() -> None:
    if settings.DEMO_MODE:
        raise RuntimeError("Set DEMO_MODE=false for live LLM task-flow evals.")
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is required for live task-flow evals.")

    print(f"MODEL={settings.LLM_MODEL}")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    failures: list[str] = []
    try:
        sessions = SessionService(db)
        chat = ChatService(db)
        checkins = CheckInService(db)
        alex_id, jordan_id, group_id = await seed_couple(db)

        joint = sessions.create_session(
            str(group_id),
            "joint",
            str(alex_id),
            [str(alex_id), str(jordan_id)],
        )
        print(f"JOINT_SESSION={joint.id}")

        turn_1 = await chat.process_joint_message(
            str(joint.id),
            str(alex_id),
            "I want to propose a task: I will send Jordan a short check-in text by 6pm every day for 1 day.",
        )
        print(f"THERAPIST_1={compact(turn_1['content'])}")
        print(f"TASK_PROPOSALS_1={turn_1.get('task_proposals', [])}")

        tasks = print_tasks(db, "AFTER_TURN_1")
        if len(tasks) != 1:
            failures.append(f"expected_one_task_after_turn_1_got_{len(tasks)}")
        else:
            task = tasks[0]
            if task.assigned_to != alex_id:
                failures.append("self_proposed_task_not_assigned_to_sender")
            if task.status != "proposed":
                failures.append(f"self_proposed_task_status_{task.status}")
            if task.assigned_approved is not True or task.verifier_approved is not False:
                failures.append("self_proposed_task_approval_flags_wrong")

            accept = await checkins.decide_task(str(task.id), str(jordan_id), "accepted")
            print(f"PARTNER_ACCEPT={accept}")
            db.refresh(task)
            if task.status != "active":
                failures.append(f"partner_accept_did_not_activate_task_{task.status}")

            done = await checkins.mark_checkin_done(str(task.id), str(alex_id))
            print(f"ASSIGNEE_DONE={done}")
            db.refresh(task)
            if task.status != "completed":
                failures.append(f"one_day_self_tracked_task_not_completed_{task.status}")

        turn_2 = await chat.process_joint_message(
            str(joint.id),
            str(jordan_id),
            "I want to propose another task: Alex will lead one 10 minute Sunday planning conversation this week.",
        )
        print(f"THERAPIST_2={compact(turn_2['content'])}")
        print(f"TASK_PROPOSALS_2={turn_2.get('task_proposals', [])}")

        tasks = print_tasks(db, "AFTER_TURN_2")
        open_tasks = [task for task in tasks if task.status == "proposed"]
        if len(open_tasks) != 1:
            failures.append(f"expected_one_new_proposed_task_after_turn_2_got_{len(open_tasks)}")
        else:
            task = open_tasks[0]
            if task.assigned_to != alex_id:
                failures.append("partner_proposed_task_not_assigned_to_partner")
            if task.assigned_approved is not False or task.verifier_approved is not True:
                failures.append("partner_proposed_task_approval_flags_wrong")

            reject = await checkins.decide_task(str(task.id), str(alex_id), "rejected", "I need a different plan.")
            print(f"ASSIGNEE_REJECT={reject}")
            db.refresh(task)
            if task.status != "rejected":
                failures.append(f"assignee_reject_did_not_reject_{task.status}")

        if failures:
            print("LIVE_TASK_FLOW_EVAL_FAILURES=" + repr(failures))
            raise SystemExit(2)

        print("LIVE_TASK_FLOW_EVAL_FAILURES=[]")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
