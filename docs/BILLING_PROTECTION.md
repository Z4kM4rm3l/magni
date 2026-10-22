# Magni Billing Protection — 4-Layer System

## The 4 Layers (any one stops a runaway bill)

| Layer | Where | What it does | Fails |
|-------|-------|-------------|-------|
| 1 | client_guard.py | Atomic DB row lock (SELECT FOR UPDATE) — race condition proof | Closed |
| 2 | client_guard.py | Any exception = request blocked, never silently allowed | Closed |
| 3 | client_guard.py | Global 10,000 req/day cap across ALL clients combined | Closed |
| 4 | Claude Console | Monthly spend limit on the API workspace Magni uses | External |

## Layer 4 Setup (DO THIS ONCE in the Claude Console)

1. Go to platform.claude.com/settings/workspaces and create a workspace for Magni
   (the default workspace can't have its own limit)
2. Create Magni's API key inside that workspace and use it as ANTHROPIC_API_KEY
3. Open the workspace → Spend limits → Monthly limit → Change limit
   - Start around $20/month and raise it as customers grow
4. Under Email notification, add an alert so you hear about it before the cap

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
At roughly $0.001 per message on Claude Haiku 5.5, that caps daily exposure around $10.

**Layer 4 — Everything above fails simultaneously**
If all 3 code layers somehow fail, the Claude Console workspace spend limit
stops API calls once the monthly limit is reached. Final hard stop, completely
outside your codebase.

## Adjusting the global cap

In core/client_guard.py:
    GLOBAL_DAILY_CAP = 10_000  # increase as client base grows

Each counted request is one customer message, which makes two or three
model calls. Rough cost on Claude Haiku 5.5 is ~$0.001 per message:
  10,000 messages = ~$10/day maximum exposure
  50,000 messages = ~$50/day maximum exposure
