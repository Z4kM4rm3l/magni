# Magni

## The AI leasing assistant for rental businesses

Magni is a website chat assistant for **long-term rental property managers** and **seasonal / vacation rental operators**. It sits on the business's own website and does what a leasing agent does at 9 pm on a Sunday:

- answers prospects' questions from the business's own listings and policies,
- asks a fixed, Fair Housing friendly set of pre-qualifying questions,
- books tours or calls on the business's real calendar,
- and sends every lead to the owner the moment it comes in.

It also points current residents to the right contact, works in English and Spanish, and gives owners an Insights report built only from their own data.

Magni is the flagship product of **Lemram Industries**.

**Role:** Solo Architect & AI Software Engineer
**Status:** Launch-ready, pre-deployment (Railway deploy and live smoke test next)
**Stack:** Python, Flask, gunicorn, PostgreSQL, SQLAlchemy, Alembic, Claude Haiku 5.5 (Anthropic), Stripe, Railway

---

## What it does for an owner

| | |
|---|---|
| **Answers prospects 24/7** | Rent, fees, pets, parking, amenities, availability, how to apply. Facts come only from the owner's knowledge base. If it isn't there, the assistant says the team will confirm, and never guesses. |
| **Pre-qualifies the same way every time** | The owner picks from a fixed catalog of questions (property, move-in or stay dates, contact details, occupants or guests, lease length, budget, pets, posted criteria). Every prospect gets the same questions, in the same order, with the same wording. |
| **Captures every lead** | Finished intakes alert the owner by email (and SMS once Twilio is approved), with the answers, a reply-to straight to the prospect, and a calendar invite when a tour is booked. Prospects who leave contact details and go quiet are sent as partial leads after 20 minutes. |
| **Books tours and calls** | The assistant offers real open times from the owner's weekly hours, books outright only on an exact pick ("2") or an explicit day and time, and confirms anything looser. Prospects get a confirmation, an .ics invite, a cancel link and a day-before reminder. |
| **Multiple properties** | Owners list their properties. The property question becomes a numbered pick, so every lead names a property the owner recognizes. |
| **Current residents** | Residents asking about repairs, rent or their lease get the owner's office contact, plus a "call 911 first" line in an emergency. They never become leads. |
| **English and Spanish** | The conversation's language is detected once and kept. Every fixed reply, question, booking time, email and the cancel page have fixed Spanish wording. |
| **After-hours tagging** | Leads that arrive outside the owner's hours are tagged, so they get the first call in the morning. |
| **Insights** | A data-only report: chat-to-tour funnel, where prospects stop, questions the assistant couldn't answer, what residents complain about, results by property, and rule-based suggestions that show the numbers behind them. No AI writes any of it. |
| **Branded as the owner's** | Bot name, accent color and welcome message are the owner's. The assistant introduces itself by the owner's bot and business name. Starter plans show a small "Powered by Magni" link. |

---

## How a chat turn works

```text
Visitor on the owner's website
        │
        ▼
Widget  (/widget/chat)
  - allowed-website check, per-visitor and per-account rate limits
        │
        ▼
Billing and usage gate  (core/client_guard.py)
  - trial / subscription state, one count per conversation,
    40-message cap per chat, global daily cap, fails closed
        │
        ▼
Orchestrator  (agents/orchestrator.py)
  ├── intake in progress?      → Lead flow (deterministic: what to ask, in what order)
  │                               ├── Lead extractor (LLM reads answers only)
  │                               └── Scheduler (open slots, parse "el sábado a las 11", book)
  ├── otherwise → Triage (LLM: prospect / current resident / other, or escalate)
  │       ├── prospect   → start the intake
  │       ├── resident   → Resolver + the owner's office contact
  │       ├── other      → Resolver (rental-only, declines off-topic)
  │       └── escalation → Escalation agent + owner alert
        │
        ▼
Fair Housing output filter (English + Spanish)  →  reply
```

The model is used for three narrow jobs: classifying the message, reading answers out of the prospect's words, and phrasing answers from the knowledge base. **What to ask, in what order, when to book and who gets alerted is ordinary code**, so it is predictable and testable.

---

## Fair Housing by design

- Questions come from a fixed catalog in `core/leasing.py`, with fixed English and Spanish wording. Owners can turn questions on or off, but never write their own.
- The assistant collects answers. It never scores, ranks, approves or rejects anyone.
- Assistance animals are never treated as pets, in either language. Accommodation requests are flagged for a person to handle.
- Every free-form rental reply passes `check_output()`, which replaces any reply describing who a home or area "suits" (families, couples, "safe neighborhood" and similar, in English and Spanish).
- Rental criteria and property names are checked for steering language before they're saved.
- Insights topics describe the property or the request, never the people asking.

This is engineering care, not legal advice. Each customer's questions and criteria should be reviewed by counsel.

---

## Multi-tenant isolation

Every tenant's data is keyed by `client_id`: knowledge articles, conversations and messages, leads, appointments and settings.

- Portal routes take the tenant **only** from the login session (`current_client_id()`), never from the request.
- Conversations are owned by `client_id + session_id`, and live chat context is namespaced by tenant, so two visitors sharing a session id never share context.
- Appointment cancel links are unguessable tokens that only allow viewing and canceling that one booking.
- Customer widget keys are publishable. They work only through `/widget/chat`, which enforces each tenant's allowed websites and rate limits.
- Operator (admin) and customer authentication are separate. Mutating requests require a CSRF token.

---

## Billing, trials and cost control

- **Plans:** Starter ($49, 1,000 conversations a month) and Pro ($99, 5,000 conversations, no "Powered by" link). There's a 14-day free trial, with Stripe Checkout and the Customer Portal.
- **Usage counts per conversation**, not per message, matching the plans. Owners get an email at 80% of their allowance and another at the limit.
- **Trials are never reset early.** A failed card keeps the assistant answering while Stripe retries. It never locks the owner out of the portal.
- **Webhooks** are signature-checked and idempotent, and always re-read the subscription live from Stripe. `scripts/reconcile_billing.py` repairs anything a missed webhook left behind.
- **AI spend has four independent layers:** a database-locked per-tenant counter, fail-closed error handling, a global daily cap, and the Anthropic workspace spend limit. See `docs/BILLING_PROTECTION.md`.

---

## Data, privacy and retention

- Chat transcripts are deleted after **12 months**, and leads and appointments after **24 months**, by a daily job (`scripts/apply_retention.py`). Owners can export leads to CSV at any time.
- Phone numbers and emails are masked in every Insights example.
- Links in emails and chat come only from `PUBLIC_BASE_URL`, never from a request's Host header. The app refuses to start in production without it.
- The landing-page demo never emails or texts anyone, and its data is cleared daily.

---

## Insights without invention

`core/insights.py` never calls a model. It counts the tenant's own chats, leads and tours, and applies fixed rules. Each suggestion appears only once it clears a minimum sample size (for example, at least 3 unanswered questions on one topic), and it shows its basis ("based on: 4 of 6 unfinished intakes"). Topics come from fixed English and Spanish keyword lists. A message can match several topics or none, and off-topic questions are shown as they are.

Examples of what it can tell an owner:

- "Add a knowledge base article about parking: 3 prospect questions about parking got 'our team will confirm'."
- "Most prospects who stop, stop at Budget."
- "Residents' top issue: plumbing and leaks", with their own words.

---

## Project structure

```text
magni/
├── app.py                    Flask app: landing page + demo, signup/login/password reset,
│                             client portal and /api/portal/*, admin console, /appt/<token>
├── agents/
│   ├── orchestrator.py       entry point for every chat turn
│   ├── triage_agent.py       prospect / resident / other, or escalate
│   ├── resolver_agent.py     rental-only answers grounded in the knowledge base
│   ├── escalation_agent.py   human handoff decisions
│   └── lead_agent.py         reads intake answers out of the prospect's message
├── core/
│   ├── llm.py                the only place that calls the model (Claude via the Anthropic SDK)
│   ├── leasing.py            question catalog, config validation, Fair Housing guardrails
│   ├── lead_flow.py          deterministic intake, booking step, alerts, partial-lead sweep, reminders
│   ├── scheduling.py         availability, reply parsing (EN/ES), booking, cancel, .ics, after-hours
│   ├── i18n.py               English and Spanish for every fixed reply, language detection
│   ├── insights.py           data-only owner insights
│   ├── leads.py              leads, CSV export, database-side analytics counts
│   ├── retention.py          12 / 24 month retention
│   ├── client_guard.py       usage + billing gate before any model call
│   ├── billing_policies.py   plans, trial, entitlement, branding rule
│   ├── notifier.py           email (any SMTP provider) and SMS
│   ├── knowledge_base.py     tenant-scoped knowledge base
│   └── ...                   auth, CSRF, rate limits, models, security headers
├── routes/
│   ├── widget.py             embeddable widget, allowed websites, rate limits
│   └── billing.py            Stripe Checkout, Customer Portal, webhooks
├── migrations/versions/      Alembic 0001 to 0008 (Alembic is the schema authority)
├── scripts/                  cron jobs and one-off tools (sweep, usage reset, retention,
│                             demo account, billing reconcile, admin password hash)
├── templates/                landing, portal, admin, auth and appointment pages
├── tests/                    11 offline suites + 3 live-model checks
└── docs/
    ├── LAUNCH_CHECKLIST.md   Railway, Stripe, email, cron and smoke-test steps
    └── BILLING_PROTECTION.md AI cost safety layers
```

---

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in SECRET_KEY, DATABASE_URL, etc.
python app.py                   # dev server; production runs gunicorn (see Procfile)
```

The app runs `alembic upgrade head` on startup. `python scripts/create_demo_client.py` creates the sample apartment community used by the demo on `/`.

### Tests

```bash
python tests/test_leasing_mvp.py          # billing, intake, Fair Housing guardrails, alerts
python tests/test_scheduler.py            # booking, cancel, reminders, widget CORS
python tests/test_portal_isolation.py     # tenant isolation + CSRF
python tests/test_launch_fixes.py         # usage per conversation, trial resets, key + login hardening
python tests/test_rental_focus.py         # rental-only prompts, analytics, tiers, demo, migration 0007
python tests/test_market_features.py      # residents, after hours, Spanish, multiple properties
python tests/test_account_emails.py       # SMTP provider, forgot password, usage warnings
python tests/test_retention.py            # retention + database-side counts
python tests/test_insights.py             # data-only insights, minimum samples, masking
python tests/test_conversation_ownership.py
python tests/test_orchestrator.py
```

Each suite prints `ALL PASSED`. They run on in-memory SQLite, with the model, email, SMS and Stripe stubbed, so no keys are needed. To run the database-heavy suites against real Postgres, point `MAGNI_TEST_DATABASE_URL` at an empty database that has had `alembic upgrade head`. `test_triage_agent.py`, `test_resolver_agent.py` and `test_escalation_agent.py` call the real Claude API and need `ANTHROPIC_API_KEY`.

### Deploy

Follow `docs/LAUNCH_CHECKLIST.md`. It covers the Railway web service and the cron services (lead sweep, monthly usage reset, demo reset, retention), the environment variables, Stripe products and webhooks, the email provider with SPF / DKIM / DMARC, and a step-by-step smoke test.

---

## Engineering decisions

**Deterministic flow, narrow model jobs.** The model classifies, extracts and phrases. Code decides what to ask, when to book and who to alert. *Why:* the parts that carry legal and business risk should behave the same way every time, and be testable.

**Tenant + resource ownership everywhere.** Records are addressed by the owning tenant together with their id, never by id alone. *Why:* a globally unique id is not authorization.

**Fail closed on money.** The usage gate locks the tenant's row, counts before the model is called, and blocks on any error. *Why:* a bug should cost a missed reply, not an unbounded bill.

**Never hold a database transaction across a model call.** Leads are loaded, detached, worked on, and saved in short transactions. *Why:* a slow model response must not hold locks or exhaust the connection pool.

**Insights are counted, not generated.** *Why:* an owner acting on a suggestion needs to trust that it reflects their data, so every suggestion carries its numbers.

**Explicit migrations.** Every schema change is a numbered Alembic migration. *Why:* production databases need reproducible changes.

**Single process, honest about it.** Rate limits, the login lockout and the global cap live in one gunicorn worker's memory, so production runs one replica. *Why:* that's right for this scale. Redis comes in only when horizontal scaling does.

---

## Status and what's next

**Built:** self-serve signup with a 14-day trial and Stripe billing, the portal, the embeddable widget, the rental intake with instant alerts, partial-lead capture, the built-in scheduler, the resident path, Spanish, multiple properties, after-hours tags, Insights, forgot password, usage warnings, data retention, and the landing page with a live demo.

**Next:**
- Railway deploy and live smoke test
- Transactional email provider on a verified domain
- Twilio A2P 10DLC registration for SMS
- Terms of service and privacy policy, and attorney review of the question set
- The first paying customer

**Later, as real usage shows the need:** CRM and calendar connectors (Google, Outlook), semantic retrieval for large knowledge bases, shared rate limits for multiple instances, and an evaluation suite that measures routing and grounding quality over time.

---

## Development approach

Magni was built end to end as a solo project. AI coding agents were used as development tools, while product direction, architecture, security boundaries and validation stayed under my direction.

The project goes deliberately past a chatbot demo, into the problems that show up when an LLM becomes part of a real SaaS product. How should tenant identity flow through AI workflows? How do you keep a model inside legal guardrails like Fair Housing? How do you bound AI cost? How do you test AI behavior and authorization boundaries instead of trusting a demo?

---

## About

**Zakary Marmel**, Solo Architect & AI Software Engineer

I build AI-powered software that combines LLM capabilities with traditional application engineering. My operations background shapes how I approach it. The goal isn't just impressive model output. It's software that fits real workflows, has clear authorization and failure boundaries, controls cost, and stays understandable when something goes wrong.

GitHub: Z4kM4rm3l
Portfolio: zakarymarmel.netlify.app
