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
| `SECRET_KEY` | long random string (already set) |
| `DATABASE_URL` | Railway Postgres (already set) |
| `GEMINI_API_KEY` | already set |
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

Prospects who give a phone or email and then stop replying get sent to the owner after 20 minutes. Live chat traffic triggers this about once a minute, but add a cron service so it also runs overnight:

- New Railway service from the same repo
- Config-as-code path: `railway.sweep.json` (runs `python scripts/sweep_leads.py` every 10 minutes)
- Same env vars as the web service

## 6. Smoke test (test mode)

1. Sign up at `/signup`. The portal should say **Free trial: 14 days left**.
2. **Leasing assistant** card: set Business type to *Long-term rentals*, add posted criteria, an application link, and your own email/phone for alerts. Save.
3. **Knowledge base**: use a suggested article (for example *Available units and pricing*) and add one real unit with a price.
4. **Install**: paste the embed snippet into a test page, and add that page's domain under Appearance → Allowed website domains.
5. Chat as a prospect: "Is the 2 bedroom available in January?" Answer every question. You should get the alert email (and text, if Twilio is set up), and the lead should show in **Leads**.
6. In a new chat, say "I have an emotional support dog." The assistant should not quote pet rules, and the lead should be flagged as an accommodation request.
7. **Choose Starter** → pay with card `4242 4242 4242 4242`. Back in the portal it should still show the trial (first charge happens when the trial ends). In Stripe, the subscription should show as *trialing*.
8. **Manage billing** should open the Stripe customer portal. Cancel there, and within a few seconds the portal should say *Your subscription has ended*, and the widget should reply with the neutral "unavailable" message.

## 7. Before your first real customer

- Have a Florida real estate attorney (or the customer's counsel) look at the question list in `core/leasing.py` and the customer's posted criteria. The assistant is built to be Fair Housing friendly, but that isn't legal advice.
- Switch Stripe to live mode and replace the four Stripe variables with live values.
