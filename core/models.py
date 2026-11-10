# core/models.py
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, JSON, Text, ForeignKey, Index, Float
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

    # ── Owner alerts ("needs a person") ───────────────────────────────────────
    # Where alerts go. Blank email means the account email.
    notify_email = Column(String, nullable=True)
    notify_phone = Column(String, nullable=True)  # +1XXXXXXXXXX, optional SMS

    # ── Widget config ─────────────────────────────────────────────────────────
    bot_name        = Column(String, default="Assistant")
    primary_color   = Column(String, default="#f59e0b")
    welcome_message = Column(String, default="Hi! How can I help you today?")
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
    escalations = relationship(
        "Escalation",
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
        Index("ix_conv_started", "started_at"),  # retention
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


class Escalation(Base):
    """A chat that needs a person. One active record per (client, chat session).

    Written before any email goes out, so a failed or unconfigured alert never
    loses the request: the owner still sees it in the portal, and the email is
    retried.
    """
    __tablename__ = "escalations"

    id         = Column(String, primary_key=True, index=True)  # str(uuid.uuid4())
    client_id  = Column(
        String,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(String, nullable=False, index=True)

    # collecting = asked the visitor for contact details, still waiting
    # confirming = had contact details from earlier in the chat; asked the
    #              visitor whether they are the best way to reach them
    # open       = the owner needs to follow up
    # closed     = the owner marked it handled
    status       = Column(String, nullable=False, default="collecting")
    source       = Column(String, nullable=True)   # gate|triage|resolver|error
    action       = Column(String, nullable=True)   # collect_contact|human_handoff
    reason       = Column(Text, nullable=True)
    last_message = Column(Text, nullable=True)     # what the visitor said that triggered it
    transcript   = Column(Text, nullable=True)     # the chat so far, kept for alert retries
    lang         = Column(String, nullable=True)   # language of the fixed replies (en|es)
    ask_count    = Column(Integer, default=0)      # turns since asking for contact with none given

    contact_name  = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)

    # ── Owner alert bookkeeping ───────────────────────────────────────────────
    notified_at         = Column(DateTime(timezone=True), nullable=True)  # first alert delivered
    contact_notified_at = Column(DateTime(timezone=True), nullable=True)  # alert carrying the contact delivered
    notify_attempts     = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship("Client", back_populates="escalations")

    __table_args__ = (
        Index("ix_esc_client_created", "client_id", "created_at"),
        Index("ix_esc_client_session", "client_id", "session_id"),
        Index("ix_esc_created", "created_at"),  # retention
    )


class StripeEvent(Base):
    """Processed Stripe webhook event ids, so a redelivered event is applied once."""
    __tablename__ = "stripe_events"

    id          = Column(String, primary_key=True)  # Stripe event id (evt_...)
    type        = Column(String, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
