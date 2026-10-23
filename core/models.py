# core/models.py
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, JSON, Text, ForeignKey, Index, Float, text
)
from sqlalchemy.orm import relationship
from core.db import Base

class Client(Base):
    __tablename__ = "clients"

    # ── Identity ──────────────────────────────────────────────────────────────
    id            = Column(String, primary_key=True, index=True)  # str(uuid.uuid4())
    api_key       = Column(String, unique=True, index=True, nullable=False)
    business_name = Column(String, nullable=True)
    email         = Column(String, nullable=True)
    password_hash = Column(String, nullable=True)  # set only for self-signup clients (client portal login)
    notes         = Column(String, nullable=True, default="")
    created_at    = Column(DateTime(timezone=True), nullable=True)

    # ── Billing state ─────────────────────────────────────────────────────────
    tier                   = Column(String, default="starter")
    is_active              = Column(Boolean, default=True)
    stripe_customer_id     = Column(String, unique=True, index=True, nullable=True)
    stripe_subscription_id = Column(String, unique=True, index=True, nullable=True)
    monthly_limit          = Column(Integer, nullable=True)
    monthly_used           = Column(Integer, default=0)
    billing_period_start   = Column(DateTime(timezone=True), nullable=True)
    billing_period_end     = Column(DateTime(timezone=True), nullable=True)
    # Self-serve trial + Stripe subscription state. Kept separate from
    # is_active (operator suspension) so a failed payment never locks an
    # owner out of the portal where they would fix it. NULL trial_ends_at
    # means an operator-managed account with no trial clock.
    trial_ends_at          = Column(DateTime(timezone=True), nullable=True)
    subscription_status    = Column(String, nullable=True)  # trialing|active|past_due|canceled|unpaid|incomplete...
    billing_synced_at      = Column(DateTime(timezone=True), nullable=True)  # created-time of last applied Stripe event

    # ── Vertical / leasing assistant ──────────────────────────────────────────
    vertical    = Column(String, nullable=True, default="long_term")  # long_term|seasonal
    lead_config = Column(JSON, nullable=True)  # validated by core.leasing.normalize_lead_config

    # ── Widget config ─────────────────────────────────────────────────────────
    bot_name        = Column(String, default="Leasing Assistant")
    primary_color   = Column(String, default="#f59e0b")
    welcome_message = Column(String, default="Hi! Looking for a place? I can check availability and book a tour.")
    allowed_domains = Column(JSON, nullable=True)   # ["mikeshvac.com"] — jsonb in Postgres

    # ── Relationships ─────────────────────────────────────────────────────────
    # Deleting a client removes its articles (DB-level via ondelete, ORM-level
    # via cascade) so no orphaned knowledge can outlive its tenant.
    knowledge_articles = relationship(
        "KnowledgeArticle",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    conversations = relationship(
        "Conversation",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    leads = relationship(
        "Lead",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    appointments = relationship(
        "Appointment",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"

    # ── Identity ──────────────────────────────────────────────────────────────
    id        = Column(String, primary_key=True, index=True)  # preserves legacy JSON ids on import
    client_id = Column(
        String,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────────────────────
    title    = Column(String, nullable=False)
    content  = Column(Text, nullable=False)
    summary  = Column(Text, nullable=True)
    category = Column(String, nullable=False, default="general")

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    client = relationship("Client", back_populates="knowledge_articles")

    # ── Indexes ───────────────────────────────────────────────────────────────
    # Every retrieval filters by client_id; the composite also serves the
    # "most recent first" listing the admin panel does per client.
    __table_args__ = (
        Index("ix_kb_client_updated", "client_id", "updated_at"),
    )


class Conversation(Base):
    __tablename__ = "conversations"

    # ── Identity ──────────────────────────────────────────────────────────────
    # session_id is a globally unique UUID from the widget, so it doubles as the
    # natural primary key. Every conversation is owned by exactly one client.
    session_id = Column(String, primary_key=True, index=True)
    client_id  = Column(
        String,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    started_at    = Column(DateTime(timezone=True), nullable=True)
    ended_at      = Column(DateTime(timezone=True), nullable=True)
    message_count = Column(Integer, default=0)
    date          = Column(String, nullable=True)   # YYYY-MM-DD (day bucket)
    hour          = Column(Integer, nullable=True)   # 0-23 (hour bucket)

    # ── Intent ────────────────────────────────────────────────────────────────
    intents        = Column(JSON, nullable=True)     # list[str]
    primary_intent = Column(String, nullable=True)

    # ── Resolution telemetry ──────────────────────────────────────────────────
    resolved              = Column(Boolean, nullable=True)
    resolution_source     = Column(String, nullable=True)
    resolution_confidence = Column(Float, nullable=True)
    resolution_last_user  = Column(Text, nullable=True)
    resolution_last_agent = Column(Text, nullable=True)

    # ── Feedback ──────────────────────────────────────────────────────────────
    rating           = Column(Integer, nullable=True)
    feedback_comment = Column(Text, nullable=True, default="")

    # ── Relationships ─────────────────────────────────────────────────────────
    client = relationship("Client", back_populates="conversations")
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.id",
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    # Analytics always filters by client_id and windows on started_at.
    __table_args__ = (
        Index("ix_conv_client_started", "client_id", "started_at"),
    )


class Message(Base):
    __tablename__ = "messages"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String,
        ForeignKey("conversations.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role      = Column(String, nullable=False)   # "user" | "assistant"
    content   = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=True)

    conversation = relationship("Conversation", back_populates="messages")


class Lead(Base):
    """A prospect captured by the leasing assistant. One per (client, chat session).

    The assistant only COLLECTS answers to the owner's enabled questions; it never
    scores, ranks, or rejects anyone. The owner decides.
    """
    __tablename__ = "leads"

    id         = Column(String, primary_key=True, index=True)  # str(uuid.uuid4())
    client_id  = Column(
        String,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(String, nullable=False, index=True)

    # ── Flow state ────────────────────────────────────────────────────────────
    status      = Column(String, nullable=False, default="in_progress")  # in_progress|complete|abandoned
    pending_key = Column(String, nullable=True)   # question currently waiting for an answer
    reask_count = Column(Integer, default=0)
    answers     = Column(JSON, nullable=True)     # {question_key: answer}
    flags       = Column(JSON, nullable=True)     # {"accommodation_request": true, ...}

    # ── Denormalized contact (for the list view + alerts) ─────────────────────
    contact_name  = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)

    # ── Owner workflow ────────────────────────────────────────────────────────
    owner_status = Column(String, nullable=False, default="new")  # new|contacted|closed
    notified_at  = Column(DateTime(timezone=True), nullable=True)
    notify_kind  = Column(String, nullable=True)  # complete|partial|accommodation

    created_at   = Column(DateTime(timezone=True), nullable=True)
    updated_at   = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship("Client", back_populates="leads")

    __table_args__ = (
        Index("ix_leads_client_created", "client_id", "created_at"),
        Index("ix_leads_client_session", "client_id", "session_id"),
    )


class StripeEvent(Base):
    """Processed Stripe webhook event ids, so a redelivered event is applied once."""
    __tablename__ = "stripe_events"

    id          = Column(String, primary_key=True)  # Stripe event id (evt_...)
    type        = Column(String, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)


class Appointment(Base):
    """A tour / call booked through the built-in scheduler."""
    __tablename__ = "appointments"

    id         = Column(String, primary_key=True, index=True)
    client_id  = Column(String, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    lead_id    = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)

    start_at   = Column(DateTime(timezone=True), nullable=False)
    end_at     = Column(DateTime(timezone=True), nullable=False)
    status     = Column(String, nullable=False, default="booked")  # booked|canceled

    name       = Column(String, nullable=True)
    email      = Column(String, nullable=True)
    phone      = Column(String, nullable=True)
    property   = Column(String, nullable=True)

    cancel_token     = Column(String, nullable=False, unique=True, index=True)
    created_at       = Column(DateTime(timezone=True), nullable=True)
    canceled_at      = Column(DateTime(timezone=True), nullable=True)
    canceled_by      = Column(String, nullable=True)  # prospect|owner
    reminder_sent_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship("Client", back_populates="appointments")

    __table_args__ = (
        Index("ix_appt_client_start", "client_id", "start_at"),
        # One booked appointment per client per slot, enforced by the database.
        Index("uq_appt_client_slot_booked", "client_id", "start_at", unique=True,
              postgresql_where=text("status = 'booked'"), sqlite_where=text("status = 'booked'")),
        # And one booked appointment per prospect (double-taps, retries).
        Index("uq_appt_lead_booked", "lead_id", unique=True,
              postgresql_where=text("status = 'booked' AND lead_id IS NOT NULL"),
              sqlite_where=text("status = 'booked' AND lead_id IS NOT NULL")),
    )
