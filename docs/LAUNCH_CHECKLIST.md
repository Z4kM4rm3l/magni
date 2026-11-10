# Launch checklist

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
| `PUBLIC_BASE_URL` | `https://YOUR-DOMAIN` (no trailing slash). Used for Stripe return links and the portal links in alerts |
| `STRIPE_SECRET_KEY` | `sk_test_...` then `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` from step 1.3 |
| `STRIPE_PRICE_STARTER` | `price_...` for Starter |
| `STRIPE_PRICE_PROFESSIONAL` | `price_...` for Pro |
| `MAGNI_SMTP_FROM` | the address mail comes from, on your own domain (e.g. `alerts@yourdomain.com`) |
| `MAGNI_SMTP_PASSWORD` | the email provider's SMTP password / API token (see "Email sending" below) |
| `MAGNI_SMTP_HOST` / `MAGNI_SMTP_PORT` / `MAGNI_SMTP_USER` | the provider's SMTP settings. Leave unset only for a Gmail test account |
| `OPERATOR_ALERT_EMAIL` | your own email. Magni emails it when AI model calls start failing (provider outage, bad key, out of credits) and again when they recover |
| `OPERATOR_ALERT_PHONE` | optional, your US mobile for the same alert by text. Only works once the Twilio variables below are set and approved |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | optional, for SMS lead alerts (see note) |

**SMS note:** texting US numbers from a regular Twilio number requires A2P 10DLC registration (brand + campaign), which can take one to a few weeks. Email alerts work immediately; SMS turns on by itself once these three variables are set.

## Email sending

"Needs a person" alerts, lead alerts, booking confirmations, reminders, usage warnings and password resets all go through one SMTP connection. Gmail works for testing but caps sending (about 500 a day) and its mail sent "from" a business name often lands in spam. Before real customers, use a transactional email service on your own domain. Any of these work; the app needs no code change, only variables:

| Provider | `MAGNI_SMTP_HOST` | `MAGNI_SMTP_PORT` | `MAGNI_SMTP_USER` | `MAGNI_SMTP_PASSWORD` |
|---|---|---|---|---|
| Postmark (recommended for transactional mail) | `smtp.postmarkapp.com` | `587` | the Server API token | the same Server API token |
| Resend | `smtp.resend.com` | `587` | `resend` | your API key |
| SendGrid | `smtp.sendgrid.net` | `587` | `apikey` | your API key |

Check the provider's own SMTP page for the current values. Then, in the provider's dashboard, verify your domain and add the DNS records it gives you (SPF, DKIM, and a DMARC record such as `v=DMARC1; p=none; rua=mailto:you@yourdomain.com`). Send yourself a lead alert and a booking confirmation and confirm they land in the inbox, not spam. Use the same variables on the `lead-sweep` cron service.

## 3. Database

Nothing to run by hand. The app runs `alembic upgrade head` on startup, which applies every migration up to `0009` (the `escalations` table for chats that need a person). Existing accounts keep their business type; new signups start as general customer service accounts.

If you restore an old database that predates Alembic, run `alembic stamp 0001` once first (unchanged from before).

## 4. Billing safety net

- After deploying, run `python scripts/reconcile_billing.py` once (Railway shell or locally with the production `DATABASE_URL`). It re-syncs every subscription from Stripe and lists any account the old billing code suspended after a failed card even though it's paying now. Rerun with `--reactivate` to turn those back on.
- Optional: schedule the same script daily as a backstop for missed webhooks.

## 5. Sweep (scheduled job)

The sweep retries any "needs a person" alert that failed to send, sends prospects who gave a phone or email and then went quiet to the owner after 20 minutes, and sends booked prospects a reminder the day before their appointment. Live chat traffic triggers it about once a minute, but add a cron service so they also run overnight. Railway no longer lets new services use `railway.json` files, so set this up in the dashboard:

- **+ New → GitHub repo → magni** (a second service, e.g. `lead-sweep`)
- **Settings → Start Command:** `python scripts/sweep_leads.py`
- **Settings → Cron Schedule:** `*/10 * * * *` (UTC; Railway's minimum is 5 minutes)
- **Variables:** `DATABASE_URL`, `PUBLIC_BASE_URL`, and the same `MAGNI_SMTP_*` variables as the web service (and the Twilio ones once SMS is approved)

Optional: a daily cron service for `python scripts/reset_monthly_usage.py` (e.g. `0 9 * * *`) with `DATABASE_URL` and `STRIPE_SECRET_KEY`. It resets monthly conversation counts for paying accounts at each Stripe renewal and for operator-managed accounts on every run; trials are never reset.

**Data retention.** Add a daily cron service for `python scripts/apply_retention.py` (e.g. `30 6 * * *`) with `DATABASE_URL`. It deletes chat transcripts older than 12 months and leads and appointments older than 24 months (`core/retention.py`). Put the same periods in your terms of service and privacy policy.

**Landing-page demo.** Run `python scripts/create_demo_client.py` once from the Railway shell (needs `PUBLIC_BASE_URL`). It creates a sample coffee shop (a general account) that the widget on `/` uses. Then add a daily cron service for `python scripts/reset_demo_daily.py` (e.g. `0 5 * * *`) with `DATABASE_URL`, which clears the demo's usage and chats. The demo never sends emails or texts.

The web service's **Start Command** should be `gunicorn --workers 1 --threads 8 --timeout 120 app:app` (already set in `railway.json` and `Procfile`). Keep one worker and one replica: the login lockout, rate limits and global cap live in that process's memory.

## 6. Smoke test (test mode)

0. Optional live model check from your Codespace with `ANTHROPIC_API_KEY` set: `python tests/test_triage_agent.py` and `python tests/test_resolver_agent.py` call the real Claude API.

**General customer service (every account)**

1. Sign up at `/signup`. The portal should say **Free trial: 14 days left**, and **Business type** should read *General business (customer service)*.
2. **Knowledge base**: use a suggested article (for example *Hours and location* and *Returns and refunds*) and fill in real details.
3. **Appearance**: set the assistant's name, welcome message and color.
4. **Install**: paste the embed snippet into a test page, and add that page's domain under Appearance → Allowed website domains. The widget refuses to answer on any site that isn't listed (add both `example.com` and `www.example.com` if the site uses both).
5. Ask something the knowledge base covers ("What are your hours?"), then something it doesn't. The second answer should say the team will confirm, with no invented facts.
6. Say "I'd like to talk to a person." The reply should ask for your name and an email or phone number, and you should get a **Needs a person** email with the whole chat. Reply in the chat with a name and email: the assistant should confirm, a second email should arrive with those details (replying to it writes to that address), and the chat should show under **Needs a person** in the portal as *Needs follow-up*. Click **Mark handled**.

**Leasing mode (rental accounts only)**

7. In **Business type and alerts**, choose *Long-term rentals*. Add posted criteria, an application link, two property names (one per line) and a resident contact line. Tick *Let prospects book a time right in the chat*, set your hours and where to meet, and save. Add a *Available units and pricing* article with one real unit and a price.
8. Chat as a prospect: "Is the 2 bedroom available? Could I tour Saturday?" Answer the questions, then pick a time (reply with a number, or say "Saturday at 11"). You should get the lead alert with a calendar invite, the prospect email should get a confirmation with an invite and a cancel link, and the booking should show under **Appointments**. Open the cancel link and cancel; the time should open again.
9. In a new chat, write in Spanish: "Hola, ¿tienen apartamentos disponibles?" Every question and the booking times should come back in Spanish, and the lead should be tagged *Spanish*.
10. As a current resident: "My sink is leaking." The reply should include the resident contact you entered, and no lead should appear. In a new chat, say "I have an emotional support dog." The assistant should not quote pet rules, and the lead should be flagged as an accommodation request.

**Billing**

11. **Choose Starter** → pay with card `4242 4242 4242 4242`. Back in the portal it should still show the trial (first charge happens when the trial ends). In Stripe, the subscription should show as *trialing*.
12. **Manage billing** should open the Stripe customer portal. Cancel there, and within a few seconds the portal should say *Your subscription has ended*, and the widget should reply with the neutral "unavailable" message.

## 7. Before your first real customer

- For a rental customer using leasing mode: have a Florida real estate attorney (or the customer's counsel) look at the question list in `core/leasing.py` and the customer's posted criteria. The assistant is built to be Fair Housing friendly, but that isn't legal advice.
- Switch Stripe to live mode and replace the four Stripe variables with live values.
