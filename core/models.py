# core/models.py
from sqlalchemy import Column, String, Integer, Boolean, DateTime
from core.db import Base

class Client(Base):
    __tablename__ = "clients"

    id = Column(String, primary_key=True, index=True)
    api_key = Column(String, unique=True, index=True, nullable=False)
    tier = Column(String, default="starter")         # starter, professional, enterprise
    is_active = Column(Boolean, default=True)
    
    # Billing State
    stripe_customer_id = Column(String, unique=True, index=True, nullable=True)
    stripe_subscription_id = Column(String, unique=True, index=True, nullable=True)
    monthly_limit = Column(Integer, nullable=True)   # Overrides TIER_LIMITS if explicitly set
    monthly_used = Column(Integer, default=0)
    
    # Cycle Anchors for the Reset Worker
    billing_period_start = Column(DateTime(timezone=True), nullable=True)
    billing_period_end = Column(DateTime(timezone=True), nullable=True)