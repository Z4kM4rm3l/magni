# Launch checklist: leasing assistant MVP

Everything below is one-time setup. Do it in Stripe **test mode** first, run the smoke test, then repeat the Stripe steps in live mode.

## 1. Stripe

1. **Products and prices.** Create two products with monthly recurring prices:
   - Magni Starter, $49/month
   - Magni Pro, $99/month

   Copy each **price id** (`price_...`). The amounts shown in the portal come from `core/billing_policies.py` (`PLAN_PRICES`), so keep them in sync if you change prices.
2. **Customer portal.** Settings → Billing → Customer portal. Turn on: update payment method, view invoices, cancel subscription, and switch plans (add both prices).
3. **Webhook.** Developers → Webhooks → Add endpoint:
   - URL: `https://YOUR-DOMAIN/api/v1/stripe-webhook`
   - Events: `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`
   - Copy the signing secret (`whsec_...`).

## 2. Railway environment variables (web service)

| Variable | Value |
|---|---|
| `SECRET_KEY` | long random string: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference to the Railway Postgres service) |
| `ANTHROPIC_API_KEY` | Claude API key from platform.claude.com, created inside a dedicated Magni workspace with a monthly spend limit (see `docs/BILLING_PROTECTION.md`, layer 4) |
| `CLAUDE_MODEL` | optional; defaults to `claude-haiku-5-5`. Change it here to move models, no code change needed |
| `CLAUDE_FAST_MODEL` | optional; model for quick routing/extraction calls (defaults to `CLAUDE_MODEL`) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | operator login; the password must be the hash from `python scripts/hash_admin_password.py` |
| `ALLOWED_ORIGINS` | same as `PUBLIC_BASE_URL` |
| `PUBLIC_BASE_URL` | `https://YOUR-DOMAIN` (no trailing slash). Used for Stripe return links and the "see all leads" link in alerts |
| `STRIPE_SECRET_KEY` | `sk_test_...` then `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` from step 1.3 |
| `STRIPE_PRICE_STARTER` | `price_...` for Starter |
| `STRIPE_PRICE_PROFESSIONAL` | `price_...` for Pro |
| `MAGNI_SMTP_FROM` | Gmail address that sends alerts |
| `MAGNI_SMTP_PASSWORD` | Gmail **app password** for that address |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | optional, for SMS lead alerts (see note) |

**SMS note:** texting US numbers from a regular Twilio number requires A2P 10DLC registration (brand + campaign), which can take one to a few weeks. Email alerts work immediately; SMS turns on by itself once these three variables are set.

## 3. Database

Nothing to run by hand. The app runs `alembic upgrade head` on startup, which applies migration `0005` (trial/subscription columns, leasing config, `leads`, `stripe_events`). Existing clients keep working exactly as before (no trial clock).

If you restore an old database that predates Alembic, run `alembic stamp 0001` once first (unchanged from before).

## 4. Billing safety net

- After deploying, run `python scripts/reconcile_billing.py` once (Railway shell or locally with the production `DATABASE_URL`). It re-syncs every subscription from Stripe and lists any account the old billing code suspended after a failed card even though it's paying now. Rerun with `--reactivate` to turn those back on.
- Optional: schedule the same script daily as a backstop for missed webhooks.

## 5. Partial-lead sweep (scheduled job)

Prospects who give a phone or email and then stop replying get sent to the owner after 20 minutes, and booked prospects get a reminder email the day before their appointment. Live chat traffic triggers both about once a minute, but add a cron service so they also run overnight. Railway no longer lets new services use `railway.json` files, so set this up in the dashboard:

- **+ New → GitHub repo → magni** (a second service, e.g. `lead-sweep`)
- **Settings → Start Command:** `python scripts/sweep_leads.py`
- **Settings → Cron Schedule:** `*/10 * * * *` (UTC; Railway's minimum is 5 minutes)
- **Variables:** `DATABASE_URL`, `MAGNI_SMTP_FROM`, `MAGNI_SMTP_PASSWORD`, `PUBLIC_BASE_URL` (and the Twilio ones once SMS is approved)

Optional: a daily cron service for `python scripts/reset_monthly_usage.py` (e.g. `0 9 * * *`) with `DATABASE_URL` and `STRIPE_SECRET_KEY`. It resets monthly conversation counts for paying accounts at each Stripe renewal and for operator-managed accounts on every run; trials are never reset.

**Landing-page demo.** Run `python scripts/create_demo_client.py` once from the Railway shell (needs `PUBLIC_BASE_URL`). It creates a sample apartment community that the widget on `/` uses. Then add a daily cron service for `python scripts/reset_demo_daily.py` (e.g. `0 5 * * *`) with `DATABASE_URL`, which clears the demo's usage, leads and bookings. The demo never sends emails or texts.

The web service's **Start Command** should be `gunicorn --workers 1 --threads 8 --timeout 120 app:app` (already set in `railway.json` and `Procfile`). Keep one worker and one replica: the login lockout, rate limits and global cap live in that process's memory.

## 6. Smoke test (test mode)

0. Optional live model check from your Codespace with `ANTHROPIC_API_KEY` set: `python tests/test_triage_agent.py` and `python tests/test_resolver_agent.py` call the real Claude API.

1. Sign up at `/signup`. The portal should say **Free trial: 14 days left**.
2. **Leasing assistant** card: set Business type to *Long-term rentals*, add posted criteria, an application link, and your own email/phone for alerts. Save.
3. **Knowledge base**: use a suggested article (for example *Available units and pricing*) and add one real unit with a price.
4. **Install**: paste the embed snippet into a test page, and add that page's domain under Appearance → Allowed website domains. The widget refuses to answer on any site that isn't listed (add both `example.com` and `www.example.com` if the site uses both).
5. **Online booking**: in the Leasing assistant card, tick *Let prospects book a time right in the chat*, set your hours and where to meet, and save.
6. Chat as a prospect: "Is the 2 bedroom available? Could I tour Saturday?" Answer the questions, then pick a time from the list (reply with a number, or say "Saturday at 11"). You should get the lead alert with a calendar invite, the prospect email should get a confirmation with an invite and a cancel link, and the booking should show under **Appointments**.
7. Open the cancel link from the confirmation email and cancel. You should get a cancellation email, and the time should be open again.
8. In a new chat, say "I have an emotional support dog." The assistant should not quote pet rules, and the lead should be flagged as an accommodation request.
9. **Choose Starter** → pay with card `4242 4242 4242 4242`. Back in the portal it should still show the trial (first charge happens when the trial ends). In Stripe, the subscription should show as *trialing*.
10. **Manage billing** should open the Stripe customer portal. Cancel there, and within a few seconds the portal should say *Your subscription has ended*, and the widget should reply with the neutral "unavailable" message.

## 7. Before your first real customer

- Have a Florida real estate attorney (or the customer's counsel) look at the question list in `core/leasing.py` and the customer's posted criteria. The assistant is built to be Fair Housing friendly, but that isn't legal advice.
- Switch Stripe to live mode and replace the four Stripe variables with live values.
