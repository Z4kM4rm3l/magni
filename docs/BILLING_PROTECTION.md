# Magni Billing Protection — 4-Layer System

## The 4 Layers (any one stops a runaway bill)

| Layer | Where | What it does | Fails |
|-------|-------|-------------|-------|
| 1 | client_guard.py | Atomic DB row lock (SELECT FOR UPDATE) — race condition proof | Closed |
| 2 | client_guard.py | Any exception = request blocked, never silently allowed | Closed |
| 3 | client_guard.py | Global 10,000 req/day cap across ALL clients combined | Closed |
| 4 | Google Cloud Console | Hard spending cap — Gemini API stops accepting calls | External |

## Layer 4 Setup (DO THIS ONCE in Google Cloud Console)

1. Go to console.cloud.google.com
2. Billing → Budgets & Alerts → Create Budget
3. Set:
   - Scope: Your project
   - Amount: $5/month (adjust as you grow)
   - Alert thresholds: 50%, 90%, 100%
4. Budget Actions → Disable billing at 100%
   - This physically cuts off the Gemini API when your budget hits $5
   - No code required, no way to bypass it

## What each layer protects against

**Layer 1 — Race condition**
Two requests arrive simultaneously. Both read monthly_used=499.
Without FOR UPDATE: both pass, you serve 2 extra convos.
With FOR UPDATE: second request waits, reads 500, gets blocked.

**Layer 2 — Code/DB failure**
DB connection drops mid-check.
Old code: except block returned True (fail open = unlimited requests).
New code: except block returns False (fail closed = request blocked).

**Layer 3 — Multi-client simultaneous bug**
All clients hit limits simultaneously due to a bug.
Individual checks might fail but global cap of 10,000/day stops everything.
At $0.0001/call that caps daily exposure at $1 maximum.

**Layer 4 — Everything above fails simultaneously**
If all 3 code layers somehow fail, Google itself stops accepting API calls
when your budget hits $5. Final hard stop — completely outside your codebase.

## Adjusting the global cap

In core/client_guard.py:
    GLOBAL_DAILY_CAP = 10_000  # increase as client base grows

At current pricing (~$0.0001/call):
  10,000 calls = ~$1.00/day maximum exposure
  50,000 calls = ~$5.00/day maximum exposure
  100,000 calls = ~$10.00/day maximum exposure
