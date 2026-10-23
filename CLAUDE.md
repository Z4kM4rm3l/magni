# Magni: notes for Claude Code

Multi-tenant AI leasing assistant for rental businesses only: long-term rental property managers and seasonal / vacation rental operators (people looking to rent, or guests booking a shorter stay). Home sales are out of scope. Every account is `long_term` or `seasonal`. Python/Flask, SQLAlchemy + Alembic on PostgreSQL, Claude Haiku 5.5 via the Anthropic SDK, Stripe billing, deployed on Railway.

## Owner preferences
- Zak works at the architecture level and relies on you for code. Explain what you changed and why in plain language.
- Don't use em dashes in anything written for him (messages, docs, UI copy).
- Ship in small, tested steps: one branch per feature, tests passing before every commit, squash-merge PRs into `main`.

## Run things
```bash
pip install -r requirements.txt
python tests/test_leasing_mvp.py        # billing, leasing intake, Fair Housing guardrails, alerts
python tests/test_scheduler.py          # built-in scheduler (booking, cancel, reminders, widget CORS)
python tests/test_portal_isolation.py   # tenant isolation + CSRF
python tests/test_launch_fixes.py       # usage per conversation, trial resets, key + login hardening
python tests/test_rental_focus.py       # rental-only prompts, leasing analytics, tiers, demo account, migration 0007
python tests/test_market_features.py    # residents, after-hours tags, Spanish intake + booking, multiple properties
python tests/test_account_emails.py     # SMTP provider, forgot password, 80% / limit usage warnings
python tests/test_retention.py          # 12 / 24 month retention, database-side analytics counts (also runs on Postgres)
python tests/test_insights.py           # owner insights: data-only rules, minimum samples, masking (also runs on Postgres)
python tests/test_conversation_ownership.py
python tests/test_orchestrator.py
```
Each suite prints `ALL PASSED` (orchestrator prints `PASS`). They run on in-memory SQLite with the LLM, email, SMS, and Stripe stubbed, so no keys are needed. To run a suite against real Postgres, point `MAGNI_TEST_DATABASE_URL` at an empty database that has had `alembic upgrade head`.

`tests/test_triage_agent.py`, `test_resolver_agent.py`, `test_escalation_agent.py` call the real Claude API and need `ANTHROPIC_API_KEY`.

## Map
- `app.py`: Flask app, landing page with the live demo (`/`), admin + client portal routes, `/api/portal/*` JSON APIs, public `/appt/<token>` cancel page.
- `agents/`: `orchestrator.py` (entry point for every chat turn), `triage_agent`, `resolver_agent`, `escalation_agent`, `lead_agent` (extracts intake answers).
- `scripts/create_demo_client.py` / `reset_demo_daily.py`: the landing-page demo account (a sample rental). It never emails or texts anyone.
- `core/llm.py`: the ONLY place that calls the model. `generate()` / `chat()`; model from `CLAUDE_MODEL` (default `claude-haiku-5-5`). Tests stub these two functions.
- `core/leasing.py`: question catalog, presets, config validation, Fair Housing guardrails (`check_output`, `detect_accommodation`).
- `core/lead_flow.py`: deterministic intake engine (what to ask, in what order), booking step, alerts, partial-lead sweep, reminders.
- `core/scheduling.py`: availability, reply parsing ("2", "Saturday at 11am", "el sábado a las 11"), booking, cancel, .ics invites, `is_after_hours`.
- `core/i18n.py`: English and Spanish for every fixed reply, plus `detect_lang`. A lead's language is set once (`lead.flags["lang"]`) and kept. Owner-facing text stays English.
- `core/insights.py`: the portal's Insights card. Counts and fixed rules over the tenant's own chats, leads and tours, never a model call. Every suggestion has a minimum sample and a "basis" with its numbers; examples are real messages with contact details masked.
- `core/billing_policies.py` + `routes/billing.py`: 14-day trial, Stripe Checkout / Customer Portal, webhooks (idempotent, always re-read the subscription live from Stripe).
- `core/client_guard.py`: per-request usage + billing gate before any model call.
- `routes/widget.py`: embeddable widget (`/widget/magni.js`, `/widget/chat`), per-client allowed domains, rate limits.
- `migrations/versions/`: Alembic is the schema authority; `init_db()` runs `alembic upgrade head` on startup. Add a new numbered migration for every model change.
- `docs/LAUNCH_CHECKLIST.md`: Railway, Stripe, and smoke-test steps. `docs/BILLING_PROTECTION.md`: cost safety layers.

## Rules that must not regress
- **Tenant isolation:** portal routes take `client_id` only from the session (`current_client_id()`), never from the request. Every query is scoped by `client_id`.
- **CSRF:** mutating portal/admin requests go through `client_login_required` / `api_login_required`, which enforce the token.
- **Fair Housing:** every prospect gets the same questions in the same order with fixed wording (fixed per language: English `text`, Spanish `text_es`); the assistant never scores, ranks, or rejects anyone; assistance animals are never pets; rental replies pass `check_output()`, which checks English and Spanish phrasing. Don't add owner-written questions. Any new fixed reply needs both languages in `core/i18n.py`.
- **Scheduler safety:** only an exact pick ("2") or an explicit day + clock time books outright; anything looser is confirmed first. One booked appointment per slot and per prospect, enforced by partial unique indexes.
- **Billing:** a failed payment never sets `is_active=False`; billing state lives in `subscription_status`. Visitor-facing errors stay neutral (`VISITOR_UNAVAILABLE`).
- **Never hold a DB transaction open across an LLM call** (see `_load_detached` / `_save` in `lead_flow.py`).
- **Links** in emails and chat come from `PUBLIC_BASE_URL` only; the app refuses to start in production without it.
- **Insights never invent anything:** no LLM in `core/insights.py`; a new suggestion needs a minimum sample, a `basis` with its numbers, and no guessed cause. Topics describe the property or request, never the people (no schools, "safe areas", families).
- **Retention:** chats are deleted after 12 months, leads and appointments after 24 (`core/retention.py`, daily `scripts/apply_retention.py`). Analytics count in the database (`func.count`), never by loading a tenant's rows into Python.

## Required env vars (Railway)
`SECRET_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `PUBLIC_BASE_URL`, `ALLOWED_ORIGINS`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` (hash), `MAGNI_SMTP_FROM`, `MAGNI_SMTP_PASSWORD`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PROFESSIONAL`. Email provider: `MAGNI_SMTP_HOST`, `MAGNI_SMTP_PORT`, `MAGNI_SMTP_USER` (Gmail if unset; use a transactional provider in production). Optional: `CLAUDE_MODEL`, `CLAUDE_FAST_MODEL`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.

## Status (Oct 2026)
Built: trial + Stripe billing, leasing pre-qualification with instant email/SMS alerts, Leads tab, built-in scheduler with confirmations/reminders, Claude Haiku 5.5. Not yet done: Railway deploy + live smoke test, Twilio A2P 10DLC registration for SMS, a first paying customer.
