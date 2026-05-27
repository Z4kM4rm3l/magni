# scripts/reset_monthly_usage.py
import os
from datetime import datetime, timezone
from sqlalchemy.orm import Session
import stripe
from core.db import SessionLocal
from core.models import Client
from core.utils import logger

# Initialize your stripe secret key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def reset_monthly_usage():
    """
    Daily/hourly background job to reset monthly_used counts based on 
    actual Stripe current_period rollover.
    """
    db: Session = SessionLocal()
    now = datetime.now(timezone.utc)
    
    try:
        # Fetch active accounts whose registered DB cycle window has passed
        expired_clients = db.query(Client).filter(
            Client.is_active == True,
            Client.stripe_subscription_id.isnot(None),
            Client.billing_period_end <= now
        ).all()
        
        for client in expired_clients:
            try:
                # Query Stripe source‑of‑truth directly
                sub = stripe.Subscription.retrieve(client.stripe_subscription_id)
                stripe_end = datetime.fromtimestamp(sub.current_period_end, tz=timezone.utc)
                
                # Check if Stripe moved the window forward
                if stripe_end > client.billing_period_end:
                    logger.info(f"Resetting usage for Client {client.id}. Cycle rolled over on Stripe.")
                    client.monthly_used = 0
                    client.billing_period_start = datetime.fromtimestamp(sub.current_period_start, tz=timezone.utc)
                    client.billing_period_end = stripe_end
                    db.add(client)
                else:
                    logger.warning(
                        f"Client {client.id} period ended in DB, but Stripe hasn't advanced. "
                        f"Retrying next run."
                    )
                    
            except stripe.error.StripeError as se:
                logger.error(f"Stripe retrieval failed for client {client.id}: {se}")
                
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Usage reset worker encountered a critical failure: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    reset_monthly_usage()