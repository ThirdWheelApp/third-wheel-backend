"""
Pydantic Schemas for Request/Response Validation

Defines data structures for API endpoints.
"""

from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime


# User Schemas
class UserCreate(BaseModel):
    email: EmailStr
    name: str
    supabase_user_id: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    created_at: datetime

    class Config:
        from_attributes = True


# Group Schemas
class GroupCreate(BaseModel):
    partner_id: str  # The other partner (current user is inferred from JWT)


class GroupResponse(BaseModel):
    id: str
    partner1_id: str
    partner2_id: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# Session Schemas
class SessionCreate(BaseModel):
    group_id: str
    session_type: str  # 'private' or 'joint'
    participants: List[str]
    scheduled_for: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    group_id: str
    type: str
    status: str
    participants: List[str]
    started_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# CheckIn Schemas
class CheckInResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    status: str
    assigned_to: str
    verifier_id: Optional[str]
    requires_verification: bool
    progress: dict
    frequency: str

    class Config:
        from_attributes = True


class CheckInApproval(BaseModel):
    role: str  # 'assigned' or 'verifier'


class CheckInVerification(BaseModel):
    status: str  # 'verified' or 'needs_work'
    feedback: Optional[str] = None
