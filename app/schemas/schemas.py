"""
Pydantic Schemas for Request/Response Validation

Defines data structures for API endpoints.
Uses camelCase aliases for frontend compatibility.
"""

from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, date


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase for field aliases."""
    components = string.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])


class CamelCaseModel(BaseModel):
    """
    Base model that automatically converts snake_case to camelCase.

    All response models should inherit from this.
    """
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        alias_generator=to_camel,
    )


# ============================================================================
# User Schemas
# ============================================================================

class UserCreate(BaseModel):
    """Schema for creating a new user."""
    email: EmailStr
    name: str
    supabase_user_id: Optional[str] = Field(None, alias="supabaseUserId")


class UserResponse(CamelCaseModel):
    """Schema for user API responses."""
    id: str
    email: str
    name: str
    created_at: datetime


# ============================================================================
# Group Schemas
# ============================================================================

class GroupCreate(BaseModel):
    """Schema for creating a relationship group."""
    partner_id: str = Field(..., alias="partnerId")

    model_config = ConfigDict(populate_by_name=True)


class GroupResponse(CamelCaseModel):
    """
    Schema for group API responses.

    Note: Frontend uses 'relationshipId' for group IDs.
    The 'id' field maps to relationshipId in frontend context.
    """
    id: str
    partner1_id: str
    partner2_id: str
    status: str
    created_at: datetime


# ============================================================================
# Session Schemas
# ============================================================================

class SessionCreate(BaseModel):
    """Schema for creating a therapy session."""
    group_id: str = Field(..., alias="groupId")
    session_type: str = Field(..., alias="sessionType")  # 'private' or 'joint'
    participants: List[str]
    scheduled_for: Optional[str] = Field(None, alias="scheduledFor")
    invite_message: Optional[str] = Field(None, alias="inviteMessage")

    model_config = ConfigDict(populate_by_name=True)


class SessionResponse(CamelCaseModel):
    """Schema for session API responses."""
    id: str
    group_id: str  # Frontend uses this as relationshipId
    type: str
    status: str
    participants: List[str]
    created_by: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    invite_message: Optional[str] = None
    created_at: datetime


# ============================================================================
# Message Schemas
# ============================================================================

class MessageResponse(CamelCaseModel):
    """Schema for message API responses."""
    id: str
    session_id: str
    sender_id: str
    sender_name: Optional[str] = None
    content: str
    sequence_number: int
    message_type: str = "message"
    privacy_level: Optional[int] = None  # 1-5 for private sessions
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None


class MessageCreate(BaseModel):
    """Schema for sending a message."""
    content: str
    privacy_level: Optional[int] = Field(None, alias="privacyLevel")

    model_config = ConfigDict(populate_by_name=True)


# ============================================================================
# CheckIn Schemas
# ============================================================================

class CheckInCreate(BaseModel):
    """Schema for creating a check-in."""
    title: str
    description: Optional[str] = None
    assigned_to: str = Field(..., alias="assignedTo")
    verifier_id: Optional[str] = Field(None, alias="verifierId")
    requires_verification: bool = Field(False, alias="requiresVerification")
    frequency: str = "daily"  # 'daily', 'weekly'
    duration_days: int = Field(7, alias="durationDays")

    model_config = ConfigDict(populate_by_name=True)


class CompletionHistoryEntry(CamelCaseModel):
    """Schema for a single completion history entry."""
    date: str  # YYYY-MM-DD format
    completed: bool


class CheckInResponse(CamelCaseModel):
    """Schema for check-in API responses."""
    id: str
    group_id: str  # Frontend uses as relationshipId
    title: str
    description: Optional[str] = None
    status: str
    assigned_to: str
    verifier_id: Optional[str] = None
    requires_verification: bool
    assigned_approved: bool = False
    verifier_approved: Optional[bool] = None
    progress: Dict[str, Any]  # {completed: int, total: int}
    frequency: Optional[str] = None
    next_check_date: Optional[date] = None
    duration_days: Optional[int] = None  # Derived from progress.total
    completion_history: Optional[List[CompletionHistoryEntry]] = None
    verification_feedback: Optional[str] = None
    created_from_session: Optional[str] = None
    created_at: datetime


class CheckInApproval(BaseModel):
    """Schema for approving a check-in."""
    role: str  # 'assigned' or 'verifier'


class CheckInVerification(BaseModel):
    """Schema for verifying a check-in completion."""
    status: str  # 'verified' or 'needs_work'
    feedback: Optional[str] = None


class CheckInMarkDone(BaseModel):
    """Schema for marking a check-in as done."""
    notes: Optional[str] = None


# ============================================================================
# Notification Schemas
# ============================================================================

class NotificationResponse(CamelCaseModel):
    """Schema for notification API responses."""
    id: str
    user_id: str
    type: str  # 'check_in_assigned', 'session_end_requested', etc.
    data: Dict[str, Any]
    read: bool
    created_at: datetime


class NotificationMarkRead(BaseModel):
    """Schema for marking a notification as read."""
    pass  # No body needed


# ============================================================================
# WebSocket Message Schemas
# ============================================================================

class WSIncomingMessage(BaseModel):
    """Schema for incoming WebSocket messages from client."""
    type: str  # 'message', 'typing_start', 'typing_stop'
    content: Optional[str] = None
    privacy_level: Optional[int] = Field(None, alias="privacyLevel")

    model_config = ConfigDict(populate_by_name=True)


class WSOutgoingMessage(CamelCaseModel):
    """Schema for outgoing WebSocket messages to client."""
    type: str  # 'message', 'typing', 'sync', 'error', 'stream_start', etc.
    message_id: Optional[str] = None
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    content: Optional[str] = None
    timestamp: Optional[str] = None
    sequence_number: Optional[int] = None
    suggest_end_session: Optional[bool] = None

    # For sync messages
    session_status: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None

    # For typing indicators
    user_id: Optional[str] = None
    is_typing: Optional[bool] = None

    # For streaming
    token: Optional[str] = None

    # For errors
    message: Optional[str] = None


# ============================================================================
# Health Check Schemas
# ============================================================================

class HealthResponse(CamelCaseModel):
    """Schema for health check responses."""
    status: str
    service: Optional[str] = None
    version: Optional[str] = None
    environment: Optional[str] = None
    database: Optional[str] = None
    llm_service: Optional[str] = None
