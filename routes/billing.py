"""
routes/billing.py

Stripe billing.

  POST /api/v1/stripe-webhook          Stripe -> Magni subscription sync
  POST /api/portal/billing/checkout    logged-in client starts a subscription (Stripe Checkout)
  POST /api/portal/billing/manage      logged-in client opens the Stripe Customer Portal

Webhook guarantees:
  * Idempotent: each Stripe event id is recorded in stripe_events inside the
    same transaction as its effects, so a redelivered event is applied once.
  * Order-proof: subscription state is re-read live from Stripe on every event,
    so out-of-order or same-second deliveries can't leave stale state behind.
  * Only the subscription on file can end service; an abandoned or duplicate
    checkout can't pause a client who is still on trial or paying.
  * A failed payment never suspends the account (is_active stays an operator
    switch). Billing state lives in subscription_status, and the widget gate
    in core/billing_policies decides what still answers.
"""
import os
import traceback
from datetime import datetime, timezone

import stripe
from flask import Blueprint, request, jsonify
from sqlalchemy.exc import IntegrityError

from core.db import SessionLocal
from core.models import Client, StripeEvent
from core.billing_policies import (
    TIER_LIMITS, SELF_SERVE_PLANS, price_id_for, plan_for_price_id, _aware,
)
# NOTE: every subscription change is re-read live from Stripe (retrieve_subscription)
# rather than trusted from the event payload.
from core.client_auth import client_login_required, current_client_id
from core.utils import logger

billing_bp = Blueprint('billing', __name__)
portal_billing_bp = Blueprint('portal_billing', __name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")


def _ts(epoch):
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc) if epoch else None


def _as_dict(obj):
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


# ── Webhook ────────────────────────────────────────────────────────────────
@billing_bp.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        logger.error(f"Stripe Webhook Signature Error: {e}")
        return jsonify({"error": "Invalid signature"}), 400

    event = _as_dict(event)
    event_id = event.get("id")
    event_type = event.get("type", "")
    created = _ts(event.get("created")) or datetime.now(timezone.utc)
    obj = event.get("data", {}).get("object", {}) or {}

    db = SessionLocal()
    try:
        if event_id:
            if db.get(StripeEvent, event_id):
                return jsonify({"status": "duplicate"}), 200
            db.add(StripeEvent(id=event_id, type=event_type, received_at=datetime.now(timezone.utc)))
            try:
                db.flush()
            except IntegrityError:
                # A concurrent delivery of the same event won the insert.
                db.rollback()
                return jsonify({"status": "duplicate"}), 200

        if event_type == "checkout.session.completed":
            handle_checkout_completed(db, obj, created)
        elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
            handle_subscription_updated(db, obj, created)
        elif event_type == "customer.subscription.deleted":
            handle_subscription_deleted(db, obj, created)
        elif event_type in ("invoice.payment_failed", "charge.failed"):
            handle_payment_failed(db, obj, created)

        db.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        db.rollback()
        logger.error(f"Webhook processing failed: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal processing error"}), 500
    finally:
        db.close()


LIVE_STATUSES = {"active", "trialing", "past_due"}
DEAD_STATUSES = {"canceled", "incomplete_expired"}


def _mark_synced(client, created):
    last = _aware(client.billing_synced_at)
    if created and (last is None or created >= last):
        client.billing_synced_at = created


def _find_client(db, *, client_id=None, subscription_id=None, customer_id=None):
    if client_id:
        c = db.get(Client, client_id)
        if c:
            return c
    if subscription_id:
        c = db.query(Client).filter(Client.stripe_subscription_id == subscription_id).first()
        if c:
            return c
    if customer_id:
        return db.query(Client).filter(Client.stripe_customer_id == customer_id).first()
    return None


def _set_tier(client, tier):
    if tier and tier in TIER_LIMITS:
        client.tier = tier
        client.monthly_limit = TIER_LIMITS[tier]


def retrieve_subscription(sub_id: str, fallback: dict | None = None) -> dict:
    """Live subscription state from Stripe. Event payloads are snapshots that can
    arrive out of order (several in the same second), so state is always taken
    from Stripe itself. A network/API error raises, the webhook returns 500, and
    Stripe redelivers later."""
    try:
        return _as_dict(stripe.Subscription.retrieve(sub_id))
    except stripe.InvalidRequestError:
        if fallback is not None:
            return fallback
        raise


def apply_subscription(db, sub: dict, created=None, hint_client_id=None):
    """Make the client's billing columns match one subscription's live state."""
    meta = sub.get("metadata") or {}
    sub_id = sub.get("id")
    status = (sub.get("status") or "").lower()
    client = _find_client(
        db,
        client_id=meta.get("client_id") or hint_client_id,
        subscription_id=sub_id,
        customer_id=sub.get("customer"),
    )
    if not client:
        logger.warning(f"Subscription {sub_id} matches no client")
        return None
    current = client.stripe_subscription_id
    current_live = bool(current) and (client.subscription_status or "") in LIVE_STATUSES

    if current and current != sub_id and current_live:
        # The client already has a working subscription on file. Never let a
        # different one (an old, duplicate, or abandoned checkout) change it.
        if status in LIVE_STATUSES:
            logger.critical(
                f"Client {client.id[:8]} has two live subscriptions ({current}, {sub_id}). "
                f"Keeping {current}; cancel and refund the other in the Stripe dashboard."
            )
        return client

    if status in DEAD_STATUSES:
        # Only the subscription on file can end service. An abandoned checkout's
        # expired subscription must not pause a client who is still on trial.
        if current == sub_id:
            client.subscription_status = "canceled"
            client.stripe_subscription_id = None
            logger.warning(f"Subscription ended for client {client.id[:8]}; assistant paused")
        _mark_synced(client, created)
        return client

    client.stripe_subscription_id = sub_id
    if sub.get("customer"):
        client.stripe_customer_id = sub["customer"]
    client.subscription_status = status or client.subscription_status

    items = (sub.get("items") or {}).get("data") or []
    first = items[0] if items else {}
    price = first.get("price") or {}
    _set_tier(client, meta.get("tier") or (price.get("metadata") or {}).get("tier") or plan_for_price_id(price.get("id")))

    # Period fields live on the subscription in older API versions and on the
    # item in newer ones.
    start = sub.get("current_period_start") or first.get("current_period_start")
    end = sub.get("current_period_end") or first.get("current_period_end")
    if start:
        client.billing_period_start = _ts(start)
    if end:
        client.billing_period_end = _ts(end)
    _mark_synced(client, created)
    logger.info(f"Synced subscription for client {client.id[:8]}: {client.subscription_status}")
    return client


def handle_checkout_completed(db, checkout_session, created=None):
    metadata = checkout_session.get("metadata") or {}
    client_id = metadata.get("client_id") or checkout_session.get("client_reference_id")
    if not client_id:
        logger.error("Stripe Checkout missing client_id in metadata/client_reference_id.")
        return
    client = db.get(Client, client_id)
    if not client:
        logger.error(f"Stripe Checkout for unknown client {client_id}")
        return
    if checkout_session.get("customer") and not client.stripe_customer_id:
        client.stripe_customer_id = checkout_session["customer"]
    if checkout_session.get("subscription"):
        apply_subscription(db, retrieve_subscription(checkout_session["subscription"]),
                           created, hint_client_id=client_id)
    logger.info(f"Checkout completed for client {client_id[:8]}")


def handle_subscription_updated(db, subscription, created=None):
    apply_subscription(db, retrieve_subscription(subscription.get("id"), fallback=subscription), created)


def handle_subscription_deleted(db, subscription, created=None):
    # A deleted subscription is still retrievable (status canceled); the payload
    # is the fallback if it isn't.
    live = retrieve_subscription(subscription.get("id"), fallback={**subscription, "status": "canceled"})
    apply_subscription(db, live, created)


def handle_payment_failed(db, obj, created=None):
    # Never cut service off here. Stripe retries the card and the subscription's
    # live status (past_due, then active or canceled) is what we record.
    sub_id = obj.get("subscription")
    if sub_id:
        apply_subscription(db, retrieve_subscription(sub_id), created)
    logger.warning(f"Payment failed (customer {obj.get('customer')}); Stripe will retry")


# ── Portal: checkout + customer portal ─────────────────────────────────────
def _base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or request.host_url).rstrip("/")


@portal_billing_bp.route("/api/portal/billing/checkout", methods=["POST"])
@client_login_required
def portal_checkout():
    cid = current_client_id()
    plan = ((request.get_json() or {}).get("plan") or "starter").strip()
    if plan not in SELF_SERVE_PLANS:
        return jsonify({"error": "Unknown plan"}), 400
    price_id = price_id_for(plan)
    if not (stripe.api_key and price_id):
        logger.error(f"Checkout unavailable: STRIPE_SECRET_KEY or {SELF_SERVE_PLANS[plan]['price_env']} not set")
        return jsonify({"error": "Billing isn't set up yet. Please contact support."}), 503

    db = SessionLocal()
    try:
        # Row lock serializes double-clicks / two tabs for the same client.
        client = db.query(Client).filter(Client.id == cid).with_for_update().first()
        if not client:
            return jsonify({"error": "Client not found"}), 404
        already = "You already have a subscription. Use Manage billing to change plans."
        if client.stripe_subscription_id and client.subscription_status in LIVE_STATUSES:
            return jsonify({"error": already}), 409

        # One Stripe customer per client, created up front, so every checkout
        # and subscription for this client hangs off the same customer.
        if not client.stripe_customer_id:
            cust = stripe.Customer.create(
                email=client.email or None,
                name=client.business_name or None,
                metadata={"client_id": client.id},
            )
            client.stripe_customer_id = cust.id
            db.flush()  # keep the row lock until the checkout session exists
        else:
            subs = _as_dict(stripe.Subscription.list(customer=client.stripe_customer_id, status="all", limit=20))
            if any((x.get("status") or "") in LIVE_STATUSES for x in subs.get("data", [])):
                return jsonify({"error": already}), 409
            # Reuse an open checkout for the same plan instead of opening a second one.
            open_sessions = _as_dict(stripe.checkout.Session.list(customer=client.stripe_customer_id, status="open", limit=10))
            for sess in open_sessions.get("data", []):
                if (sess.get("metadata") or {}).get("tier") == plan and sess.get("url"):
                    return jsonify({"url": sess["url"]})
                try:
                    stripe.checkout.Session.expire(sess["id"])
                except stripe.StripeError:
                    pass

        meta = {"client_id": client.id, "tier": plan}
        sub_data = {"metadata": meta}
        # Card now, first charge when the free trial ends (Stripe needs 48h+ lead time).
        trial_end = _aware(client.trial_ends_at)
        now = datetime.now(timezone.utc)
        if trial_end and (trial_end - now).total_seconds() > 48 * 3600:
            sub_data["trial_end"] = int(trial_end.timestamp())

        params = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": client.id,
            "metadata": meta,
            "subscription_data": sub_data,
            "allow_promotion_codes": True,
            "success_url": f"{_base_url()}/portal?billing=success",
            "cancel_url": f"{_base_url()}/portal?billing=cancel",
        }
        params["customer"] = client.stripe_customer_id
        session = stripe.checkout.Session.create(**params)
        db.commit()  # releases the lock; a second tab now sees the open session
        return jsonify({"url": session.url})
    except stripe.StripeError as e:
        db.commit()  # keep a newly created customer id even if checkout failed
        logger.error(f"Stripe checkout error for client {cid[:8]}: {e}")
        return jsonify({"error": "Couldn't reach the payment provider. Please try again."}), 502
    finally:
        db.close()


@portal_billing_bp.route("/api/portal/billing/manage", methods=["POST"])
@client_login_required
def portal_manage_billing():
    cid = current_client_id()
    db = SessionLocal()
    try:
        client = db.get(Client, cid)
        if not client or not client.stripe_customer_id:
            return jsonify({"error": "No billing account yet. Choose a plan first."}), 400
        if not stripe.api_key:
            return jsonify({"error": "Billing isn't set up yet. Please contact support."}), 503
        session = stripe.billing_portal.Session.create(
            customer=client.stripe_customer_id,
            return_url=f"{_base_url()}/portal",
        )
        return jsonify({"url": session.url})
    except stripe.StripeError as e:
        logger.error(f"Stripe portal error for client {cid[:8]}: {e}")
        return jsonify({"error": "Couldn't reach the payment provider. Please try again."}), 502
    finally:
        db.close()
