# core/models.py
from sqlalchemy import Column, String, Integer, Boolean, DateTime, JSON
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
