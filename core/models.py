# core/models.py
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, JSON, Text, ForeignKey, Index
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

    # ── Widget config ─────────────────────────────────────────────────────────
    bot_name        = Column(String, default="Magni")
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
