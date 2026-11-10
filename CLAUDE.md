# Magni: notes for Claude Code

Multi-tenant AI customer service assistant that any business can brand and embed on its website. It answers from the business's own knowledge base and hands the chat to a person when one is needed. There are no business types or verticals: every account is the same general assistant. Python/Flask, SQLAlchemy + Alembic on PostgreSQL, Claude Haiku 5.5 via the Anthropic SDK, Stripe billing, deployed on Railway.

## Owner preferences
- Zak works at the architecture level and relies on you for code. Explain what you changed and why in plain language.
- Don't use em dashes in anything written for him (messages, docs, UI copy).
- Ship in small, tested steps: one branch per feature, tests passing before every commit, squash-merge PRs into `main`.

## Run things
```bash
pip install -r requirements.txt
python tests/test_injection.py          # prompt-injection defenses: input patterns, prompt structure, output check
python tests/test_escalations.py        # "needs a person": contact capture, fail-safe routing, alerts, portal API, migrations 0009 / 0010
python tests/test_billing.py            # trial, Stripe checkout / webhooks, usage gate, rate limits
python tests/test_portal_isolation.py   # tenant isolation + CSRF
python tests/test_launch_fixes.py       # usage per conversation, trial resets, key + login hardening
python tests/test_account_emails.py     # SMTP provider, forgot password, 80% / limit usage warnings
python tests/test_retention.py          # 12 month retention, database-side analytics counts (also runs on Postgres)
python tests/test_llm_health.py         # operator alert when model calls fail, cooldown, recovery notice
python tests/test_conversation_ownership.py
python tests/test_orchestrator.py
```
Each suite prints `ALL PASSED` (orchestrator prints `PASS`). They run on in-memory SQLite with the LLM, email, SMS, and Stripe stubbed, so no keys are needed. To run a suite against real Postgres, point `MAGNI_TEST_DATABASE_URL` at an empty database that has had `alembic upgrade head`.

These call the real Claude API and need `ANTHROPIC_API_KEY`:
- `python tests/test_injection_live.py`: the attack corpus (`tests/injection_corpus.py`) against the real model. Run it before launch and after ANY change to a prompt, the model, or `core/guard.py`. `--dry-run` checks the harness without a key.
- `tests/test_triage_agent.py`, `test_resolver_agent.py`, `test_escalation_agent.py`.

## Map
- `app.py`: Flask app, landing page with the live demo (`/`), admin + client portal routes, `/api/portal/*` JSON APIs.
- `agents/`: `orchestrator.py` (entry point for every chat turn), `triage_agent` (product / account / general, or escalate), `resolver_agent` (answers from the knowledge base), `escalation_agent`.
- `core/guard.py`: prompt-injection checks that don't depend on the model. `check_input` (fixed patterns, fixed reply, no model call) and `check_output` (every model-written reply: no recited instructions, no human claim, no claimed action).
- `core/escalations.py`: chats that need a person. The durable record, the fixed high-risk patterns (`high_risk`), deterministic contact capture, owner alerts and their retries, analytics counts.
- `core/contact.py`: reads an email or US phone number out of text. No model.
- `core/llm.py`: the ONLY place that calls the model. `generate()` / `chat()`; model from `CLAUDE_MODEL` (default `claude-haiku-5-5`). Tests stub these two functions.
- `core/llm_health.py`: operator alert when model calls fail (3 in 5 minutes), at most every 30 minutes, plus one "working again" notice. Hooked in `llm._call`.
- `core/knowledge_base.py`: tenant-scoped knowledge base. Small ones go to the model whole; larger ones use keyword retrieval.
- `core/client_manager.py`: accounts, defaults (bot name, greeting, color), alert settings (`notify_email`, `notify_phone`), `client_context()`.
- `core/i18n.py`: English and Spanish for every fixed reply, plus `detect_lang`. Owner-facing text stays English.
- `core/billing_policies.py` + `routes/billing.py`: 14-day trial, Stripe Checkout / Customer Portal, webhooks (idempotent, always re-read the subscription live from Stripe).
- `core/client_guard.py`: per-request usage + billing gate before any model call.
- `routes/widget.py`: embeddable widget (`/widget/magni.js`, `/widget/chat`), per-client allowed domains, rate limits.
- `scripts/`: `retry_alerts.py` (cron: resend failed owner alerts), `apply_retention.py`, `reset_monthly_usage.py`, `reconcile_billing.py`, `create_demo_client.py` / `reset_demo_daily.py` (the landing-page demo, a sample coffee shop that never emails or texts anyone), `hash_admin_password.py`.
- `migrations/versions/`: Alembic is the schema authority; `init_db()` runs `alembic upgrade head` on startup. Add a new numbered migration for every model change. 0005 to 0007 created the old rental tables and 0010 removes them; leave that history alone.
- `docs/LAUNCH_CHECKLIST.md`: Railway, Stripe, and smoke-test steps. `docs/BILLING_PROTECTION.md`: cost safety layers.

## Rules that must not regress
- **Tenant isolation:** portal routes take `client_id` only from the session (`current_client_id()`), never from the request. Every query is scoped by `client_id`. A model prompt contains only that tenant's knowledge base.
- **CSRF:** mutating portal/admin requests go through `client_login_required` / `api_login_required`, which enforce the token.
- **Injection, structure first:** the assistant has no tools and takes no actions. Nothing a visitor typed goes into a system prompt: visitor text travels only as chat messages, or inside `<visitor_message>` / `<recent_chat>` tags in a user prompt (`sanitize_input` strips tags, so the fence can't be forged). Triage output is validated against fixed values. The widget reads only `message` and `session_id` from the browser.
- **Injection, checks second:** every model-written reply passes `guard.check_output` before a visitor sees it, and escalation wording that fails it is replaced with fixed text. Keep `guard.check_input` patterns narrow: every new pattern needs an attack in `PLAIN_ATTACKS` and must not flag anything in `BENIGN` (`tests/injection_corpus.py`). A request for a person is never refused by the guard.
- **Escalations fail safe:** a chat that needs a person is written to `escalations` before any alert is sent, and the owner is alerted whenever the gate fires, the resolver asks for a person (`[HANDOFF]` tag), or a model call fails. Only a triage-directed escalation may be talked back down by the escalation agent (`deflect`). Contact details are always collected: asked for when the visitor hasn't given any, and confirmed ("is this the best way to reach you?") when they gave some earlier in the chat. Asking for and reading contact details is code with fixed wording, never a model call, and an email or phone number is only recorded when it is literally in the visitor's message. Any new fixed reply needs both languages in `core/i18n.py`.
- **Billing:** a failed payment never sets `is_active=False`; billing state lives in `subscription_status`. Visitor-facing errors stay neutral (`VISITOR_UNAVAILABLE`).
- **Never hold a DB transaction open across an LLM call.**
- **Links** in emails and chat come from `PUBLIC_BASE_URL` only; the app refuses to start in production without it.
- **Retention:** chats and "needs a person" records are deleted after 12 months (`core/retention.py`, daily `scripts/apply_retention.py`). Analytics count in the database (`func.count`), never by loading a tenant's rows into Python.
- **No verticals in the core.** Industry-specific behavior (for example a legal or medical practice on a higher plan) should be added as an optional profile on top of the general assistant, not as branches through the pipeline.

## Required env vars (Railway)
`SECRET_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `PUBLIC_BASE_URL`, `ALLOWED_ORIGINS`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` (hash), `MAGNI_SMTP_FROM`, `MAGNI_SMTP_PASSWORD`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PROFESSIONAL`. Email provider: `MAGNI_SMTP_HOST`, `MAGNI_SMTP_PORT`, `MAGNI_SMTP_USER` (Gmail if unset; use a transactional provider in production). Operator alerts: `OPERATOR_ALERT_EMAIL`, optional `OPERATOR_ALERT_PHONE`. Optional: `CLAUDE_MODEL`, `CLAUDE_FAST_MODEL`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.

## Status (Nov 2026)
Built: general customer service for any business, the "needs a person" handoff with contact capture, prompt-injection guard and test suites, operator alert on model failures, trial + Stripe billing, self-serve portal, embeddable widget, Claude Haiku 5.5. Removed: the rental leasing mode (intake, scheduler, leads, Fair Housing rules, Insights). Not yet done: a live run of `tests/test_injection_live.py`, widget session persistence and mobile polish, Railway deploy + live smoke test, Twilio A2P 10DLC registration for SMS, a first paying customer.
