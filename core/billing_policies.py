# core/billing_policies.py

TIER_LIMITS = {
    "starter": 1000,
    "professional": 5000,
    "enterprise": 25000,
    "unlimited": -1,  # Sentinel for no cap
}