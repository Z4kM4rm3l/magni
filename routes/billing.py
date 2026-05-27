import os
from datetime import datetime, timezone
import stripe
from flask import Blueprint, request, jsonify
from core.db import SessionLocal
from core.models import Client
from core.billing_policies import TIER_LIMITS
from core.utils import logger
import traceback

billing_bp = Blueprint('billing', __name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

@billing_bp.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        logger.error(f"Stripe Webhook Signature Error: {e}")
        return jsonify({"error": "Invalid signature"}), 400

    db = SessionLocal()
    try:
        event_type = event["type"]
        obj = event["data"]["object"]

        # Route events to their specific handlers
        if event_type == "checkout.session.completed":
            handle_checkout_completed(db, obj.to_dict())
        elif event_type in ["customer.subscription.updated", "customer.subscription.created"]:
            handle_subscription_updated(db, obj.to_dict())
        elif event_type == "customer.subscription.deleted":
            handle_subscription_deleted(db, obj.to_dict())
        elif event_type == "charge.failed":
            handle_charge_failed(db, obj.to_dict())

        db.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        db.rollback()
        logger.error(f"Webhook processing failed: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal processing error"}), 500
    finally:
        db.close()

def handle_checkout_completed(db, checkout_session):
    metadata = checkout_session.get("metadata", {})
    client_id = metadata.get("client_id")
    
    if not client_id:
        logger.error("Stripe Checkout missing 'client_id' in metadata.")
        return

    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.stripe_customer_id = checkout_session.get("customer")
        client.stripe_subscription_id = checkout_session.get("subscription")
        client.tier = metadata.get("tier", "starter")
        client.monthly_limit = TIER_LIMITS.get(client.tier, 1000)
        client.is_active = True
        logger.info(f"Billing attached to Client: {client_id}")

def handle_subscription_updated(db, subscription):
    client = db.query(Client).filter(
        (Client.stripe_subscription_id == subscription.get("id")) | 
        (Client.stripe_customer_id == subscription.get("customer"))
    ).first()

    if client:
        client.is_active = (subscription.get("status") in ["active", "trialing"])
        
        items = subscription.get("items", {}).get("data", [])
        price_metadata = items[0].get("price", {}).get("metadata", {}) if items else {}
        
        tier = subscription.get("metadata", {}).get("tier") or price_metadata.get("tier", "starter")
        client.tier = tier
        client.monthly_limit = TIER_LIMITS.get(tier, 1000)
        
        if subscription.get("current_period_start"):
            client.billing_period_start = datetime.fromtimestamp(subscription["current_period_start"], tz=timezone.utc)
        if subscription.get("current_period_end"):
            client.billing_period_end = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc)
        
        logger.info(f"Synced subscription for client: {client.id}")

def handle_subscription_deleted(db, subscription):
    client = db.query(Client).filter(Client.stripe_customer_id == subscription.get("customer")).first()
    if client:
        client.is_active = False
        client.stripe_subscription_id = None
        logger.warning(f"Account suspended due to termination: {client.id}")

def handle_charge_failed(db, charge):
    """Mark client inactive if payment fails, so the Admin knows to investigate."""
    customer_id = charge.get("customer")
    client = db.query(Client).filter(Client.stripe_customer_id == customer_id).first()
    if client:
        client.is_active = False
        logger.warning(f"Payment failed for client: {client.id}. Access suspended.")