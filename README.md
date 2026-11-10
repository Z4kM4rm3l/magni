# Magni

## AI customer service for any business's website, branded as theirs

Magni is a chat assistant a business puts on its own website. It carries the business's name and colors, answers customers around the clock from the business's own knowledge base, and hands the chat to a person when one is needed:

- answers questions only from what the business has published,
- recognizes when a person is needed, collects the visitor's contact details, and emails the team the whole conversation,
- keeps every handed-off chat in the owner's portal, so nothing depends on one email arriving,
- and sets up in minutes: sign up, add FAQs and policies, paste one line of code.

Rental businesses (long-term property managers and seasonal / vacation rental operators) can switch on a **leasing mode** that also pre-qualifies renters, captures leads and books tours.

Magni is the flagship product of **Lemram Industries**.

**Role:** Solo Architect & AI Software Engineer
**Status:** Launch-ready, pre-deployment (Railway deploy and live smoke test next)
**Stack:** Python, Flask, gunicorn, PostgreSQL, SQLAlchemy, Alembic, Claude Haiku 5.5 (Anthropic), Stripe, Railway

---

## What it does for a business

| | |
|---|---|
| **Answers customers 24/7** | Products, services, pricing, hours, shipping, returns, policies. Facts come only from the owner's knowledge base. If it isn't there, the assistant says the team will confirm, and never guesses. |
| **Hands off to a person** | When a visitor asks for someone, raises a complaint, or needs something only staff can do (a refund, a change to an order), the assistant says so, asks for a name and an email or phone number with fixed wording, and alerts the owner. |
| **Never drops a handoff** | Each chat that needs a person is saved before any email is sent. The owner gets the full transcript by email (with a reply-to straight to the visitor once they share an address), sees it in the portal under **Needs a person**, and failed emails are retried automatically. |
| **Fails safe** | Clear requests for a person and legal threats are caught by fixed patterns, not just the model. If the model is unavailable, the visitor is offered a follow-up from the team instead of an error. |
| **Branded as the owner's** | Bot name, accent color and welcome message are the owner's. The assistant introduces itself by the owner's bot and business name. Starter plans show a small "Powered by Magni" link. |
| **English and Spanish** | Fixed replies (the contact questions, confirmations, the whole leasing intake) have fixed wording in both languages. Free-form answers follow the visitor's language. |
| **Self-serve** | Signup, a 14-day trial, the knowledge base, appearance, allowed websites and billing are all in the owner's portal. |

### Leasing mode (opt-in, for rental businesses)

| | |
|---|---|
| **Pre-qualifies the same way every time** | The owner picks from a fixed catalog of questions (property, move-in or stay dates, contact details, occupants or guests, lease length, budget, pets, posted criteria). Every prospect gets the same questions, in the same order, with the same wording. |
| **Captures every lead** | Finished intakes alert the owner by email (and SMS once Twilio is approved), with the answers, a reply-to straight to the prospect, and a calendar invite when a tour is booked. Prospects who leave contact details and go quiet are sent as partial leads after 20 minutes. |
| **Books tours and calls** | The assistant offers real open times from the owner's weekly hours, books outright only on an exact pick ("2") or an explicit day and time, and confirms anything looser. Prospects get a confirmation, an .ics invite, a cancel link and a day-before reminder. |
| **Multiple properties** | Owners list their properties. The property question becomes a numbered pick, so every lead names a property the owner recognizes. |
| **Current residents** | Residents asking about repairs, rent or their lease get the owner's office contact, plus a "call 911 first" line in an emergency. They never become leads. |
| **After-hours tagging** | Leads that arrive outside the owner's hours are tagged, so they get the first call in the morning. |
| **Insights** | A data-only report: chat-to-tour funnel, where prospects stop, questions the assistant couldn't answer, what residents complain about, results by property, and rule-based suggestions that show the numbers behind them. No AI writes any of it. |

---

## How a chat turn works

```text
Visitor on the business's website
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
  ├── chat waiting on contact details? → read name / email / phone from the reply (code, no model)
  ├── rental account, intake in progress? → Lead flow (deterministic)
  ├── high-risk gate (fixed patterns: "talk to a person", legal threats) → escalate
  ├── otherwise → Triage (LLM: what kind of message, or escalate)
  │       ├── question      → Resolver (answers from the knowledge base)
  │       ├── rental prospect → start the leasing intake
  │       └── escalation    → Escalation agent
  └── model failed, or the resolver asked for a person → escalate
        │
        ▼
Escalation  (core/escalations.py)
  - save the record, alert the owner, ask for contact details with fixed wording
        │
        ▼
Fair Housing output filter (rental accounts)  →  reply
```

The model is used for narrow jobs: classifying the message, phrasing answers from the knowledge base, and (in leasing mode) reading answers out of the prospect's words. **Who gets alerted, what is asked, in what order, and when to book is ordinary code**, so it is predictable and testable.

---

## Fair Housing by design (leasing mode)

- Questions come from a fixed catalog in `core/leasing.py`, with fixed English and Spanish wording. Owners can turn questions on or off, but never write their own.
- The assistant collects answers. It never scores, ranks, approves or rejects anyone.
- Assistance animals are never treated as pets, in either language. Accommodation requests are flagged for a person to handle.
- Every free-form rental reply passes `check_output()`, which replaces any reply describing who a home or area "suits" (families, couples, "safe neighborhood" and similar, in English and Spanish).
- Rental criteria and property names are checked for steering language before they're saved.
- Insights topics describe the property or the request, never the people asking.

This is engineering care, not legal advice. Each customer's questions and criteria should be reviewed by counsel.

---

## Multi-tenant isolation

Every tenant's data is keyed by `client_id`: knowledge articles, conversations and messages, chats that need a person, leads, appointments and settings.

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

- Chat transcripts (and the "needs a person" records that hold a copy) are deleted after **12 months**, and leads and appointments after **24 months**, by a daily job (`scripts/apply_retention.py`). Owners can export leads to CSV at any time.
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
│   ├── triage_agent.py       what kind of message this is, or escalate (general and rental prompts)
│   ├── resolver_agent.py     answers grounded in the knowledge base (general and rental prompts)
│   ├── escalation_agent.py   human handoff decisions
│   └── lead_agent.py         reads intake answers out of the prospect's message
├── core/
│   ├── llm.py                the only place that calls the model (Claude via the Anthropic SDK)
│   ├── escalations.py        chats that need a person: record, contact capture, alerts, retries
│   ├── leasing.py            business types, question catalog, config validation, Fair Housing guardrails
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
├── migrations/versions/      Alembic 0001 to 0009 (Alembic is the schema authority)
├── scripts/                  cron jobs and one-off tools (sweep, usage reset, retention,
│                             demo account, billing reconcile, admin password hash)
├── templates/                landing, portal, admin, auth and appointment pages
├── tests/                    12 offline suites + 3 live-model checks
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

The app runs `alembic upgrade head` on startup. `python scripts/create_demo_client.py` creates the sample coffee shop used by the demo on `/`.

### Tests

```bash
python tests/test_general_escalations.py  # general accounts, contact capture, fail-safe escalation, portal API
python tests/test_leasing_mvp.py          # billing, intake, Fair Housing guardrails, alerts
python tests/test_scheduler.py            # booking, cancel, reminders, widget CORS
python tests/test_portal_isolation.py     # tenant isolation + CSRF
python tests/test_launch_fixes.py         # usage per conversation, trial resets, key + login hardening
python tests/test_rental_focus.py         # leasing mode prompts, analytics, tiers, demo, migration 0007
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

**Fail safe on people.** A chat that needs a person is saved before anything is sent, fixed patterns catch the clearest cases without the model, and a model failure hands the chat to the team. *Why:* a missed handoff is the one failure a customer service product can't have.

**Fail closed on money.** The usage gate locks the tenant's row, counts before the model is called, and blocks on any error. *Why:* a bug should cost a missed reply, not an unbounded bill.

**Never hold a database transaction across a model call.** Leads are loaded, detached, worked on, and saved in short transactions. *Why:* a slow model response must not hold locks or exhaust the connection pool.

**Insights are counted, not generated.** *Why:* an owner acting on a suggestion needs to trust that it reflects their data, so every suggestion carries its numbers.

**Explicit migrations.** Every schema change is a numbered Alembic migration. *Why:* production databases need reproducible changes.

**Single process, honest about it.** Rate limits, the login lockout and the global cap live in one gunicorn worker's memory, so production runs one replica. *Why:* that's right for this scale. Redis comes in only when horizontal scaling does.

---

## Status and what's next

**Built:** self-serve signup with a 14-day trial and Stripe billing, the portal, the embeddable widget, general customer service answers for any business, the "needs a person" handoff with contact capture and retries, the rental intake with instant alerts, partial-lead capture, the built-in scheduler, the resident path, Spanish, multiple properties, after-hours tags, Insights, forgot password, usage warnings, data retention, and the landing page with a live demo.

**Next:**
- Railway deploy and live smoke test
- Transactional email provider on a verified domain
- Twilio A2P 10DLC registration for SMS
- Widget polish (chat that survives a page change, mobile layout), and a prompt-injection test suite
- Terms of service and privacy policy, and attorney review of the leasing question set
- The first paying customer

**Later, as real usage shows the need:** CRM and calendar connectors (Google, Outlook), semantic retrieval for large knowledge bases, shared rate limits for multiple instances, and an evaluation suite that measures routing and grounding quality over time.

---

## Development approach

Magni was built end to end as a solo project. AI coding agents were used as development tools, while product direction, architecture, security boundaries and validation stayed under my direction.

The project goes deliberately past a chatbot demo, into the problems that show up when an LLM becomes part of a real SaaS product. How should tenant identity flow through AI workflows? How do you make sure a customer who needs a person always reaches one? How do you keep a model inside legal guardrails like Fair Housing? How do you bound AI cost? How do you test AI behavior and authorization boundaries instead of trusting a demo?

---

## About

**Zakary Marmel**, Solo Architect & AI Software Engineer

I build AI-powered software that combines LLM capabilities with traditional application engineering. My operations background shapes how I approach it. The goal isn't just impressive model output. It's software that fits real workflows, has clear authorization and failure boundaries, controls cost, and stays understandable when something goes wrong.

GitHub: Z4kM4rm3l
Portfolio: zakarymarmel.netlify.app
