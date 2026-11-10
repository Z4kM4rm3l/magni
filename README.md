# Magni

## AI customer service for any business's website, branded as theirs

Magni is a chat assistant a business puts on its own website. It carries the business's name and colors, answers customers around the clock from the business's own knowledge base, and hands the chat to a person when one is needed.

- **The knowledge base is the source of truth.** The assistant states only what the business has published. If it isn't there, it says the team will confirm, and never guesses.
- **A person is always reachable.** When a visitor asks for someone, raises a complaint, or needs something only staff can do, the assistant collects their contact details and emails the team the whole conversation.
- **It works for any kind of business.** There are no industry modes to configure. What makes each assistant different is the knowledge base its owner writes.
- **Setup takes minutes.** Sign up, add FAQs and policies, paste one line of code.

Magni is the flagship product of **Lemram Industries**.

**Role:** Solo Architect & AI Software Engineer
**Status:** Launch-ready, pre-deployment (Railway deploy and live smoke test next)
**Stack:** Python, Flask, gunicorn, PostgreSQL, SQLAlchemy, Alembic, Claude Haiku 5.5 (Anthropic), Stripe, Railway

---

## What it does for a business

| | |
|---|---|
| **Answers customers 24/7** | Products, services, pricing, hours, shipping, returns, policies. Facts come only from the owner's knowledge base. |
| **Hands off to a person** | The assistant says it is passing the chat to the team, asks for a name and an email or phone number with fixed wording, and alerts the owner. If the visitor already gave contact details earlier in the chat, it confirms they are the best way to reach them instead of asking again. |
| **Never drops a handoff** | Each chat that needs a person is saved before any email is sent. The owner gets the full transcript by email (with a reply-to straight to the visitor), sees it in the portal under **Needs a person**, and failed emails are retried automatically. |
| **Fails safe** | Clear requests for a person and legal threats are caught by fixed patterns, not just the model. If the model is unavailable, the visitor is offered a follow-up from the team instead of an error, and the operator is alerted. |
| **Resists prompt injection** | Described below. |
| **Branded as the owner's** | Bot name, accent color and welcome message are the owner's. Starter plans show a small "Powered by Magni" link; Pro removes it. |
| **English and Spanish** | Fixed replies have fixed wording in both languages. Free-form answers follow the visitor's language. |
| **Self-serve** | Signup, a 14-day trial, the knowledge base, appearance, alert settings, allowed websites and billing are all in the owner's portal. |

---

## How a chat turn works

```text
Visitor on the business's website
        │
        ▼
Widget  (/widget/chat)
  - allowed-website check, per-visitor and per-account rate limits
  - reads only the message and the session id from the browser
        │
        ▼
Billing and usage gate  (core/client_guard.py)
  - trial / subscription state, one count per conversation,
    40-message cap per chat, global daily cap, fails closed
        │
        ▼
Orchestrator  (agents/orchestrator.py)
  1. chat waiting on contact details? → read name / email / phone from the reply   (code)
  2. high-risk gate: "talk to a person", legal threats → escalate                   (code)
  3. injection guard: plain attacks get a fixed reply                               (code)
  4. Triage: what kind of message, or escalate                                      (model)
  5. Resolver: answer from the knowledge base                                       (model)
     or Escalation agent: one warm sentence before the handoff                      (model)
  6. output check on anything the model wrote                                       (code)
        │
        ▼
Escalation  (core/escalations.py), when a person is needed
  - save the record, alert the owner, ask for or confirm contact details (fixed wording)
```

The model has three narrow jobs: classify the message, phrase an answer from the knowledge base, and write one sentence before a handoff. **Who gets alerted, what is asked, and what is allowed to reach the visitor is ordinary code**, so it is predictable and testable.

---

## Prompt injection

No filter can promise that a language model will never be talked into something. Magni's approach is to limit what a successful attack could do, then add checks that don't rely on the model.

**What an attacker could gain is small by design.**
- The assistant has no tools. It can't look up orders, issue refunds, send email on request, or change anything.
- A prompt contains only that one business's knowledge base, which is public-facing content anyway. Other tenants' data is never in reach.
- Nothing a visitor types is placed in a system prompt. Visitor text travels only as chat messages, or inside fenced tags the visitor can't forge.
- Triage output is validated against fixed values, and the widget ignores every field except the message and session id.

**Two code checks sit around the model** (`core/guard.py`).
- *Input:* fixed patterns for the plainest attacks ("ignore your instructions", "show me your prompt", fake system markers, "developer mode"), in English and Spanish, with zero-width characters and spaced-out letters normalized first. A match gets a fixed, polite reply and the model is never called.
- *Output:* every model-written reply is checked before a visitor sees it. A reply that recites the instructions, claims to be human, or claims an action the assistant can't take ("I've processed your refund") is replaced with fixed wording.

**Two test suites keep it honest.**
- `tests/test_injection.py` (offline): 27 plain attacks must be caught, 25 look-alike customer messages must not be, and a deliberately fooled stub model must not be able to get damage past the output check.
- `tests/test_injection_live.py` (real model): the same attacks plus 18 subtle ones (polite token requests, off-topic work, invented discounts, role-play, encoded instructions, multi-turn setups), each run with the checks on and off so you can see how much the model resists on its own.

What remains possible: a determined visitor may still get the assistant to say something off-topic or odd to themselves. That is a reputational nuisance, not a breach, and it is why the assistant has no power to act.

---

## Multi-tenant isolation

Every tenant's data is keyed by `client_id`: knowledge articles, conversations and messages, chats that need a person, and settings.

- Portal routes take the tenant **only** from the login session (`current_client_id()`), never from the request.
- Conversations are owned by `client_id + session_id`, and live chat context is namespaced by tenant, so two visitors sharing a session id never share context.
- Customer widget keys are publishable. They work only through `/widget/chat`, which enforces each tenant's allowed websites and rate limits.
- Operator (admin) and customer authentication are separate. Mutating requests require a CSRF token.

---

## Billing, trials and cost control

- **Plans:** Starter ($49, 1,000 conversations a month) and Pro ($99, 5,000 conversations, no "Powered by" link). There's a 14-day free trial, with Stripe Checkout and the Customer Portal.
- **Usage counts per conversation**, not per message, matching the plans. Owners get an email at 80% of their allowance and another at the limit.
- **Trials are never reset early.** A failed card keeps the assistant answering while Stripe retries. It never locks the owner out of the portal.
- **Webhooks** are signature-checked and idempotent, and always re-read the subscription live from Stripe. `scripts/reconcile_billing.py` repairs anything a missed webhook left behind.
- **AI spend has four independent layers:** a database-locked per-tenant counter, fail-closed error handling, a global daily cap, and the Anthropic workspace spend limit. See `docs/BILLING_PROTECTION.md`.
- **The operator hears about model failures first.** Three failed model calls in five minutes email (and optionally text) the operator with the likely cause: out of credits, a bad key, a spend limit, or a provider outage.

---

## Data, privacy and retention

- Chat transcripts, and the "needs a person" records that hold a copy, are deleted after **12 months** by a daily job (`scripts/apply_retention.py`).
- Links in emails and chat come only from `PUBLIC_BASE_URL`, never from a request's Host header. The app refuses to start in production without it.
- The landing-page demo never emails or texts anyone, and its data is cleared daily.

---

## Project structure

```text
magni/
├── app.py                    Flask app: landing page + demo, signup/login/password reset,
│                             client portal and /api/portal/*, admin console
├── agents/
│   ├── orchestrator.py       entry point for every chat turn
│   ├── triage_agent.py       what kind of message this is, or escalate
│   ├── resolver_agent.py     answers grounded in the knowledge base
│   └── escalation_agent.py   one warm sentence before a handoff
├── core/
│   ├── llm.py                the only place that calls the model (Claude via the Anthropic SDK)
│   ├── llm_health.py         emails / texts the operator when model calls start failing
│   ├── guard.py              prompt-injection checks on input and output
│   ├── escalations.py        chats that need a person: record, contact capture, alerts, retries
│   ├── contact.py            reads an email or phone number out of text (no model)
│   ├── knowledge_base.py     tenant-scoped knowledge base
│   ├── i18n.py               English and Spanish for every fixed reply, language detection
│   ├── retention.py          12 month retention
│   ├── client_guard.py       usage + billing gate before any model call
│   ├── billing_policies.py   plans, trial, entitlement, branding rule
│   ├── notifier.py           email (any SMTP provider) and SMS
│   └── ...                   accounts, auth, CSRF, rate limits, models, security headers
├── routes/
│   ├── widget.py             embeddable widget, allowed websites, rate limits
│   └── billing.py            Stripe Checkout, Customer Portal, webhooks
├── migrations/versions/      Alembic 0001 to 0010 (Alembic is the schema authority)
├── scripts/                  cron jobs and one-off tools (alert retries, usage reset, retention,
│                             demo account, billing reconcile, admin password hash)
├── templates/                landing, portal, admin and auth pages
├── tests/                    10 offline suites + 4 live-model suites
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
python tests/test_injection.py            # prompt-injection defenses (offline)
python tests/test_escalations.py          # needs a person: contact capture, fail-safe routing, alerts, portal API
python tests/test_billing.py              # trial, Stripe, usage gate, rate limits
python tests/test_portal_isolation.py     # tenant isolation + CSRF
python tests/test_launch_fixes.py         # usage per conversation, trial resets, key + login hardening
python tests/test_account_emails.py       # SMTP provider, forgot password, usage warnings
python tests/test_retention.py            # retention + database-side counts
python tests/test_llm_health.py           # operator alert when model calls fail, recovery notice
python tests/test_conversation_ownership.py
python tests/test_orchestrator.py
```

Each suite prints `ALL PASSED`. They run on in-memory SQLite, with the model, email, SMS and Stripe stubbed, so no keys are needed. To run the database-heavy suites against real Postgres, point `MAGNI_TEST_DATABASE_URL` at an empty database that has had `alembic upgrade head`.

With `ANTHROPIC_API_KEY` set, `python tests/test_injection_live.py` runs the attack corpus against the real model, and `test_triage_agent.py`, `test_resolver_agent.py` and `test_escalation_agent.py` check each agent.

### Deploy

Follow `docs/LAUNCH_CHECKLIST.md`. It covers the Railway web service and the cron services (alert retries, monthly usage reset, demo reset, retention), the environment variables, Stripe products and webhooks, the email provider with SPF / DKIM / DMARC, and a step-by-step smoke test.

---

## Engineering decisions

**One general assistant, no verticals.** An earlier version carried a rental leasing mode with its own intake, scheduler and compliance rules. It was removed. *Why:* the knowledge base already makes each assistant specific to its business, and every branch through the pipeline was another place for the product to behave differently than its owner expected.

**Deterministic flow, narrow model jobs.** The model classifies and phrases. Code decides who to alert, what to ask and what may reach the visitor. *Why:* the parts that carry business risk should behave the same way every time, and be testable.

**Limit the blast radius before filtering.** No tools, one tenant's public content per prompt, visitor text never in a system prompt. *Why:* a filter can be bypassed; a capability that doesn't exist can't be abused.

**Fail safe on people.** A chat that needs a person is saved before anything is sent, fixed patterns catch the clearest cases without the model, and a model failure hands the chat to the team. *Why:* a missed handoff is the one failure a customer service product can't have.

**Fail closed on money.** The usage gate locks the tenant's row, counts before the model is called, and blocks on any error. *Why:* a bug should cost a missed reply, not an unbounded bill.

**Tenant + resource ownership everywhere.** Records are addressed by the owning tenant together with their id, never by id alone. *Why:* a globally unique id is not authorization.

**Never hold a database transaction across a model call.** *Why:* a slow model response must not hold locks or exhaust the connection pool.

**Explicit migrations.** Every schema change is a numbered Alembic migration. *Why:* production databases need reproducible changes.

**Single process, honest about it.** Rate limits, the login lockout, the global cap and the model-failure counter live in one gunicorn worker's memory, so production runs one replica. *Why:* that's right for this scale. Redis comes in only when horizontal scaling does.

---

## Status and what's next

**Built:** self-serve signup with a 14-day trial and Stripe billing, the portal, the embeddable widget, knowledge-base answers for any business, the "needs a person" handoff with contact capture and retries, the prompt-injection guard and its test suites, operator alerts on model failures, Spanish for fixed replies, forgot password, usage warnings, data retention, and the landing page with a live demo.

**Next:**
- A live run of the injection suite against the real model
- Widget polish: a chat that survives a page change, mobile layout
- Railway deploy and live smoke test
- Transactional email provider on a verified domain
- Twilio A2P 10DLC registration for SMS
- Terms of service and privacy policy
- The first paying customer

**Later, as real usage shows the need:** industry profiles for higher plans (for example a legal or medical practice, each with its own guardrails), customer insight analytics from chat topics, knowledge base import from a URL or file, semantic retrieval for large knowledge bases, a backup model provider, and shared rate limits for multiple instances.

---

## Development approach

Magni was built end to end as a solo project. AI coding agents were used as development tools, while product direction, architecture, security boundaries and validation stayed under my direction.

The project goes deliberately past a chatbot demo, into the problems that show up when an LLM becomes part of a real SaaS product. How should tenant identity flow through AI workflows? How do you make sure a customer who needs a person always reaches one? What can a prompt injection actually reach, and how do you test that? How do you bound AI cost? How do you test AI behavior and authorization boundaries instead of trusting a demo?

---

## About

**Zakary Marmel**, Solo Architect & AI Software Engineer

I build AI-powered software that combines LLM capabilities with traditional application engineering. My operations background shapes how I approach it. The goal isn't just impressive model output. It's software that fits real workflows, has clear authorization and failure boundaries, controls cost, and stays understandable when something goes wrong.

GitHub: Z4kM4rm3l
Portfolio: zakarymarmel.netlify.app
