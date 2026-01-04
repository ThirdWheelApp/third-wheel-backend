"""
SQLAlchemy ORM Models for Third Wheel Backend

This module defines all database tables as Python classes.
Each model represents a table and includes:
- Column definitions
- Relationships between tables
- Indexes for query optimization
- Comprehensive documentation

Database Schema Overview:
- users: User accounts
- groups: Relationships/couples
- sessions: Therapy sessions (private and joint)
- messages: Chat messages within sessions
- private_user_context: Private context about users
- group_context: Shared context about relationships
- check_ins: Accountability check-ins
- llm_calls: LLM API call logging
"""

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date, Text,
    ForeignKey, ARRAY, Index, func
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.db.database import Base
import uuid
from datetime import datetime


class User(Base):
    """
    User account model.

    Represents an individual user of the platform.
    Uses Supabase user ID as primary key for seamless auth integration.

    The id field is the Supabase auth user UUID, not auto-generated.
    This ensures JWT token's 'sub' field matches our primary key.
    """
    __tablename__ = "users"

    # Primary key is the Supabase user ID (from JWT token's 'sub' field)
    id = Column(UUID(as_uuid=True), primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    # Note: These are for ORM convenience, not actual foreign keys
    created_sessions = relationship("Session", foreign_keys="[Session.created_by]", back_populates="creator")
    check_ins = relationship("CheckIn", foreign_keys="[CheckIn.assigned_to]", back_populates="assigned_user")


class Group(Base):
    """
    Relationship/couple model.

    Represents a relationship between two users.
    Contains both active and inactive (past) relationships.
    """
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partner1_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    partner2_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(50), default="active", nullable=False)  # 'active', 'inactive'

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    sessions = relationship("Session", back_populates="group", cascade="all, delete-orphan")
    group_contexts = relationship("GroupContext", back_populates="group", cascade="all, delete-orphan")
    check_ins = relationship("CheckIn", back_populates="group", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index('idx_groups_partners', 'partner1_id', 'partner2_id'),
    )


class Session(Base):
    """
    Therapy session model.

    Represents both private and joint therapy sessions.
    Tracks session lifecycle from creation through conclusion.

    Status flow:
    - active: Session in progress
    - pending_conclusion: One user requested to end
    - ending: Both users agreed, processing context
    - pending_actions: Waiting for check-in approvals
    - concluded: Session fully completed
    """
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(50), nullable=False)  # 'private' or 'joint'
    status = Column(String(50), default="active", nullable=False)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    participants = Column(ARRAY(UUID(as_uuid=True)), nullable=False)  # List of user UUIDs

    # Context accumulated during session (not committed to context tables)
    current_context = Column(JSONB, nullable=True)

    # Session end tracking
    end_requested_by = Column(UUID(as_uuid=True), nullable=True)  # First user to click "End Session"

    # Invite message for scheduled sessions (frontend compatibility)
    invite_message = Column(Text, nullable=True)

    # Timestamps
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    scheduled_for = Column(DateTime, nullable=True)  # For scheduled sessions

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    group = relationship("Group", back_populates="sessions")
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")
    check_ins = relationship("CheckIn", back_populates="source_session")

    # Indexes
    __table_args__ = (
        Index('idx_sessions_participants', 'participants', postgresql_using='gin'),
        Index('idx_sessions_status', 'status'),
    )


class Message(Base):
    """
    Chat message model.

    Represents individual messages within a session.
    Includes both user messages and therapist responses.
    """
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)

    sender_id = Column(String(255), nullable=False)  # user UUID or 'therapist'
    sender_name = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)

    # Sequence number for ordering and sync on reconnection
    sequence_number = Column(Integer, nullable=False)

    message_type = Column(String(50), default="message", nullable=False)  # 'message' or 'system'

    # Privacy level for private session messages (1-5)
    # 1 = can be referenced more freely, 5 = highly sensitive
    privacy_level = Column(Integer, nullable=True)

    # Metadata for special flags (e.g., suggest_end_session)
    metadata = Column(JSONB, nullable=True)

    timestamp = Column(DateTime, default=func.now(), nullable=False)

    # Relationships
    session = relationship("Session", back_populates="messages")

    # Indexes
    __table_args__ = (
        Index('idx_messages_session_seq', 'session_id', 'sequence_number'),
    )


class PrivateUserContext(Base):
    """
    Private context about individual users.

    Stores insights learned about a user within a specific relationship.
    Context is relationship-scoped (same user can have different context in different relationships).

    Composite Primary Key: (user_id, group_id, context_id)
    """
    __tablename__ = "private_user_context"

    # Composite primary key columns
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    context_id = Column(UUID(as_uuid=True), default=uuid.uuid4, primary_key=True)

    # Context data stored as JSONB
    # Structure: {text, secret_level, tags, category, created_at, source_session_id, ...}
    data = Column(JSONB, nullable=False)

    # Vector embedding for future semantic search (not populated in MVP)
    # TODO: Populate this when implementing vector search
    embedding = Column(String, nullable=True)  # Will be vector(1536) with pgvector

    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Indexes
    __table_args__ = (
        Index('idx_private_context_user_group', 'user_id', 'group_id'),
    )


class GroupContext(Base):
    """
    Shared context about relationships.

    Stores insights about relationship dynamics and patterns.
    Visible to both partners' private agents.
    """
    __tablename__ = "group_context"

    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    context_id = Column(UUID(as_uuid=True), default=uuid.uuid4, primary_key=True)

    # Context data stored as JSONB
    # Structure: {text, tags, participants, created_at, source_session_id, ...}
    data = Column(JSONB, nullable=False)

    # Vector embedding for future semantic search
    embedding = Column(String, nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationships
    group = relationship("Group", back_populates="group_contexts")

    # Indexes
    __table_args__ = (
        Index('idx_group_context_group', 'group_id', 'created_at'),
    )


class CheckIn(Base):
    """
    Check-in/accountability item model.

    Represents recurring tasks or practices assigned during therapy.
    Can require partner verification or be self-tracked.

    Status flow:
    - proposed: Extracted from session, awaiting approval
    - active: Approved and currently being tracked
    - awaiting_verification: User marked done, waiting for verifier
    - needs_work: Verifier requested changes
    - completed: Fully completed
    """
    __tablename__ = "check_ins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)

    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    verifier_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    status = Column(String(50), default="proposed", nullable=False)

    # Approval tracking
    assigned_approved = Column(Boolean, default=False, nullable=False)
    verifier_approved = Column(Boolean, nullable=True)  # NULL if no verification needed

    requires_verification = Column(Boolean, default=False, nullable=False)

    # Scheduling
    frequency = Column(String(50), nullable=True)  # 'daily', 'weekly'
    progress = Column(JSONB, nullable=True)  # {completed: 0, total: 7}
    next_check_date = Column(Date, nullable=True)

    # Completion history for frontend display
    # Array of {date: "YYYY-MM-DD", completed: true/false}
    completion_history = Column(JSONB, nullable=True, default=[])

    # Verification feedback
    verification_feedback = Column(Text, nullable=True)

    # Source tracking
    created_from_session = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    group = relationship("Group", back_populates="check_ins")
    assigned_user = relationship("User", foreign_keys=[assigned_to], back_populates="check_ins")
    source_session = relationship("Session", back_populates="check_ins")

    # Indexes
    __table_args__ = (
        Index('idx_checkins_assigned', 'assigned_to', 'status'),
        Index('idx_checkins_verifier', 'verifier_id', 'status'),
    )


class LLMCall(Base):
    """
    LLM API call logging model.

    Tracks all LLM API calls for debugging, cost monitoring, and prompt improvement.
    Stores summary information, not full prompts (for storage efficiency).
    """
    __tablename__ = "llm_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)

    agent_type = Column(String(50), nullable=True)  # 'private_a', 'private_b', 'joint'
    model = Column(String(100), nullable=True)

    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)

    # Store first 500 chars for debugging (not full prompts for storage reasons)
    prompt_preview = Column(Text, nullable=True)
    response_preview = Column(Text, nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Index for querying logs
    __table_args__ = (
        Index('idx_llm_calls_session', 'session_id', 'created_at'),
    )


class Notification(Base):
    """
    User notification model.

    Stores notifications for users about check-ins, session events, etc.
    Supports real-time delivery via WebSocket when user is connected.

    Notification Types:
    - check_in_assigned: New check-in needs your approval
    - check_in_verification_needed: Partner marked check-in done
    - session_end_requested: Partner wants to end session
    - post_session_actions: Pending actions after session
    """
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    type = Column(String(100), nullable=False)  # notification type identifier

    # Data payload for the notification (varies by type)
    # Examples:
    # - check_in_assigned: {checkInId, title, assignedBy}
    # - session_end_requested: {sessionId, requestedBy, requestedByName}
    data = Column(JSONB, nullable=False, default={})

    read = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Indexes
    __table_args__ = (
        Index('idx_notifications_user', 'user_id', 'read'),
        Index('idx_notifications_created', 'user_id', 'created_at'),
    )
