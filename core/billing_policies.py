# core/billing_policies.py
"""Plan limits, self-serve plans, and the single rule for whether a client's
assistant may answer right now (billing_entitlement)."""
import os
from datetime import datetime, timezone

TIER_LIMITS = {
    "starter": 1000,
    "professional": 5000,
    "enterprise": 25000,
    "unlimited": -1,  # Sentinel for no cap
}

# Self-serve free trial length for new signups.
TRIAL_DAYS = 14

# Monthly list prices (USD) for display and MRR. The amount actually charged is
# whatever the Stripe Price says; keep these in sync with the Stripe dashboard.
PLAN_PRICES = {"starter": 49, "professional": 99, "enterprise": 599}

# Plans a client can buy from the portal. price_env names the env var holding
# the Stripe Price id (price_...) for that plan.
SELF_SERVE_PLANS = {
    "starter": {
        "name": "Starter",
        "price": PLAN_PRICES["starter"],
        "price_env": "STRIPE_PRICE_STARTER",
        "blurb": "1,000 conversations/mo, lead capture, instant alerts",
    },
    "professional": {
        "name": "Pro",
        "price": PLAN_PRICES["professional"],
        "price_env": "STRIPE_PRICE_PROFESSIONAL",
        "blurb": "5,000 conversations/mo, everything in Starter, no \"Powered by Magni\" link",
    },
}

# Plans that may hide the small "Powered by Magni" link in the widget. Starter
# and trials show it: it's free marketing and a reason to upgrade.
UNBRANDED_TIERS = {"professional", "enterprise", "unlimited"}


def shows_branding(client) -> bool:
    return (client.tier or "starter") not in UNBRANDED_TIERS


# Stripe subscription statuses that keep the assistant answering. past_due is
# included so Stripe's automatic retries get a grace period before cut-off.
PAID_STATUSES = {"active", "trialing", "past_due"}
# Statuses that stop it, regardless of any trial clock.
BLOCKED_STATUSES = {"canceled", "unpaid", "incomplete", "incomplete_expired", "paused"}


def price_id_for(plan: str) -> str:
    spec = SELF_SERVE_PLANS.get(plan)
    return os.getenv(spec["price_env"], "").strip() if spec else ""


def plan_for_price_id(price_id: str) -> str | None:
    if not price_id:
        return None
    for plan in SELF_SERVE_PLANS:
        if price_id_for(plan) == price_id:
            return plan
    return None


def _aware(dt):
    """SQLite hands back naive datetimes; treat them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def billing_state(client, now: datetime | None = None) -> str:
    """One word describing where this client stands:
    managed      operator-created account, no trial clock, no subscription
    trial        inside the free trial
    trial_ended  trial over, never subscribed
    active       paying (or Stripe-side trialing)
    past_due     payment failing, Stripe is retrying (still answering)
    canceled     subscription ended or unrecoverable
    """
    now = now or datetime.now(timezone.utc)
    status = (client.subscription_status or "").lower()
    if status in BLOCKED_STATUSES:
        return "canceled"
    if client.stripe_subscription_id:
        if status == "past_due":
            return "past_due"
        return "active"  # active / trialing / not yet synced after checkout
    trial_end = _aware(client.trial_ends_at)
    if trial_end is None:
        return "managed"
    return "trial" if now < trial_end else "trial_ended"


def billing_entitlement(client, now: datetime | None = None) -> tuple[bool, str]:
    """(allowed, state). The assistant answers in every state except
    trial_ended and canceled."""
    state = billing_state(client, now)
    return state not in ("trial_ended", "canceled"), state


def trial_days_left(client, now: datetime | None = None) -> int | None:
    trial_end = _aware(client.trial_ends_at)
    if trial_end is None:
        return None
    now = now or datetime.now(timezone.utc)
    secs = (trial_end - now).total_seconds()
    return max(0, int((secs + 86399) // 86400))  # round up partial days
