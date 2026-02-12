"""
Task API Routes

POC task endpoints backed by check-ins.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Group
from app.services.checkin_service import CheckInService
from app.schemas.schemas import CheckInResponse, CheckInVerification, TaskDecisionRequest
from app.utils.auth import get_current_user
from typing import List
import uuid

router = APIRouter()


def _assert_group_access(db: Session, group_id: str, current_user_id: str) -> None:
    group = db.query(Group).filter(Group.id == uuid.UUID(group_id)).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    current_uuid = uuid.UUID(current_user_id)
    if group.partner1_id != current_uuid and group.partner2_id != current_uuid:
        raise HTTPException(status_code=403, detail="Not authorized for this group")


@router.get("/{group_id}", response_model=List[CheckInResponse])
async def get_tasks(
    group_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _assert_group_access(db, group_id, current_user_id)
    service = CheckInService(db)
    return service.get_tasks_for_group(group_id)


@router.post("/{task_id}/decision")
async def decide_task(
    task_id: str,
    payload: TaskDecisionRequest,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = CheckInService(db)
    try:
        result = await service.decide_task(
            checkin_id=task_id,
            user_id=current_user_id,
            decision=payload.decision,
            reason=payload.reason
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/checkins/{checkin_id}/complete")
async def complete_task_checkin(
    task_id: str,
    checkin_id: str,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if task_id != checkin_id:
        raise HTTPException(status_code=400, detail="POC model uses one checkin per task; IDs must match")

    service = CheckInService(db)
    try:
        checkin = await service.mark_checkin_done(checkin_id, current_user_id)
        return {"success": True, "task": checkin}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/verify")
async def verify_task(
    task_id: str,
    payload: CheckInVerification,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = CheckInService(db)
    try:
        checkin = await service.verify_checkin(
            checkin_id=task_id,
            verified_by=current_user_id,
            status=payload.status,
            feedback=payload.feedback
        )
        return {"success": True, "task": checkin}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
