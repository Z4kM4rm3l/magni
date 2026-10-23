import os
import re
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from dotenv import load_dotenv
 
# ðŸš¨ CRITICAL: Load env vars BEFORE importing core modules
load_dotenv()
 
# Stripe billing blueprint
from routes.billing import billing_bp, portal_billing_bp
from routes.widget import widget_bp
from werkzeug.middleware.proxy_fix import ProxyFix
 
from core.leads import leasing_metrics
from core.knowledge_base import (
    add_article, get_all_articles, get_article,
    update_article, delete_article, get_categories
)
from core.client_manager import (
    create_client, get_all_clients, get_client, get_client_by_api_key,
    update_client, suspend_client, reactivate_client,
    delete_client, get_client_stats
)
from core.auth import login_required, api_login_required, attempt_login, logout, get_safe_redirect
from core.client_auth import (
    authenticate_client, set_client_password, email_exists,
    log_in_client, log_out_client, current_client_id, client_login_required,
    make_reset_token, check_reset_token,
)
from core import notifier
from core.csrf import get_csrf_token, rotate_csrf_token, csrf_valid
from core.billing_policies import TRIAL_DAYS, SELF_SERVE_PLANS, TIER_LIMITS
from core.leasing import (
    VERTICALS, RENTAL_VERTICALS, QUESTION_CATALOG, available_questions,
    normalize_lead_config, LeadConfigError,
)
from core.leads import list_leads, set_owner_status, leads_csv, OWNER_STATUSES
from core import scheduling
from core.lead_flow import on_appointment_canceled, _lead_lang
from core import rate_limit
from core.security import apply_security_headers
from core.utils import sanitize_input, logger
# SQL-backed multi-tenant billing
from core.db import SessionLocal, init_db
from core.models import Client
 
app = Flask(__name__)
# Railway terminates TLS at one proxy hop; trust its X-Forwarded-* headers so
# request.host_url / remote_addr are the public ones.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.getenv("SECRET_KEY", "")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable is not set. Cannot start safely.")
# Secure cookie only over HTTPS in production; allow HTTP locally
_is_production = os.getenv("FLASK_ENV", "production") != "development"
if _is_production and not os.getenv("PUBLIC_BASE_URL", "").strip():
    raise RuntimeError(
        "PUBLIC_BASE_URL is not set (e.g. https://your-app.up.railway.app). "
        "It's required for links in booking confirmations, alerts, and Stripe returns."
    )
app.config["SESSION_COOKIE_SECURE"]   = _is_production
app.config["SESSION_COOKIE_HTTPONLY"]  = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB cap on request bodies


@app.context_processor
def _inject_csrf_token():
    # Available in every template as {{ csrf_token }} (meta tag / hidden field).
    return {"csrf_token": get_csrf_token()}
apply_security_headers(app)
 
# Register Stripe billing blueprint
app.register_blueprint(billing_bp, url_prefix='/api/v1')
app.register_blueprint(widget_bp)
app.register_blueprint(portal_billing_bp)
 
# Initialize database tables on startup
with app.app_context():
    init_db()
 
# ── PUBLIC LANDING PAGE ───────────────────────────────────────────────────
# The root page is a short product page with a live demo: the real widget,
# served for the "demo" client (a sample rental) created by
# scripts/create_demo_client.py. Demo usage runs through the same billing
# gate as every customer, capped daily by scripts/reset_demo_daily.py.
DEMO_CLIENT_ID = "demo"


@app.route("/")
def index():
    demo_key = None
    db = SessionLocal()
    try:
        demo = db.get(Client, DEMO_CLIENT_ID)
        if demo and demo.is_active:
            demo_key = demo.api_key
    finally:
        db.close()
    return render_template("landing.html", demo_key=demo_key, plans=SELF_SERVE_PLANS)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})
 
# â”€â”€ AUTH ROUTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if not csrf_valid():
            return render_template("login.html", error="Your session expired. Please try again."), 403
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        success, error_msg = attempt_login(username, password)
        if success:
            rotate_csrf_token()
            return redirect(get_safe_redirect("/admin"))
        return render_template("login.html", error=error_msg)
    return render_template("login.html", error=None)
 
@app.route("/admin/logout")
def admin_logout():
    logout()
    return redirect(url_for("admin_login"))

# ── CLIENT SELF-SERVE (signup / login / portal) ─────────────────────────────

def _embed_snippet(api_key: str) -> str:
    base = request.host_url.rstrip("/")
    return f'<script src="{base}/widget/magni.js" data-api-key="{api_key}"></script>'


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        if not csrf_valid():
            return render_template("signup.html", error="Your session expired. Please try again."), 403
        if not rate_limit.hit(f"signup:{request.remote_addr}", limit=5, window_seconds=3600):
            return render_template("signup.html", error="Too many signups from this network. Please try again later."), 429
        business_name = sanitize_input(request.form.get("business_name", "").strip())
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not business_name or not email or not password:
            return render_template("signup.html", error="All fields are required.")
        if "@" not in email or "." not in email:
            return render_template("signup.html", error="Please enter a valid email address.")
        if len(password) < 8:
            return render_template("signup.html", error="Password must be at least 8 characters.")
        if email_exists(email):
            return render_template("signup.html", error="An account with that email already exists.")
        client = create_client(business_name=business_name, email=email, tier="starter", trial_days=TRIAL_DAYS)
        set_client_password(client["client_id"], password)
        log_in_client(client["client_id"])
        rotate_csrf_token()
        logger.info(f"Self-serve signup: {business_name} ({email})")
        return redirect(url_for("client_portal"))
    return render_template("signup.html", error=None)


@app.route("/client/login", methods=["GET", "POST"])
def client_login():
    if request.method == "POST":
        if not csrf_valid():
            return render_template("client_login.html", error="Your session expired. Please try again."), 403
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not (rate_limit.hit(f"clogin-ip:{request.remote_addr}", limit=20, window_seconds=900)
                and rate_limit.hit(f"clogin-ipemail:{request.remote_addr}:{email}", limit=8, window_seconds=900)):
            return render_template("client_login.html", error="Too many attempts. Please wait 15 minutes and try again."), 429
        cid = authenticate_client(email, password)
        if cid:
            log_in_client(cid)
            rotate_csrf_token()
            return redirect(url_for("client_portal"))
        return render_template("client_login.html", error="Invalid email or password.")
    return render_template("client_login.html", error=None)


# Forgot password: the same answer whether or not the email has an account,
# so this page can't be used to find out who's a customer.
_RESET_SENT = "If that email has a Magni account, we've sent a link to reset the password. It works for one hour."


@app.route("/client/forgot", methods=["GET", "POST"])
def client_forgot():
    if request.method == "POST":
        if not csrf_valid():
            return render_template("forgot_password.html", error="Your session expired. Please try again.", sent=None), 403
        email = request.form.get("email", "").strip().lower()
        if not (rate_limit.hit(f"forgot-ip:{request.remote_addr}", limit=5, window_seconds=3600)
                and rate_limit.hit(f"forgot-email:{email}", limit=3, window_seconds=3600)):
            return render_template("forgot_password.html", error="Too many requests. Please try again later.", sent=None), 429
        token = make_reset_token(email) if email else None
        if token:
            notifier.send_password_reset(email, f"{notifier.base_url()}/client/reset/{token}")
            logger.info("Password reset link sent")
        return render_template("forgot_password.html", error=None, sent=_RESET_SENT)
    return render_template("forgot_password.html", error=None, sent=None)


@app.route("/client/reset/<token>", methods=["GET", "POST"])
def client_reset(token):
    if not rate_limit.hit(f"reset-ip:{request.remote_addr}", limit=30, window_seconds=3600):
        return "Too many requests", 429
    cid = check_reset_token(token)
    if not cid:
        return render_template("reset_password.html", token=None,
                               error="This reset link has expired or was already used. Please ask for a new one."), 400
    if request.method == "POST":
        if not csrf_valid():
            return render_template("reset_password.html", token=token, error="Your session expired. Please try again."), 403
        password = request.form.get("password", "")
        if len(password) < 8:
            return render_template("reset_password.html", token=token, error="Password must be at least 8 characters.")
        if password != request.form.get("confirm", ""):
            return render_template("reset_password.html", token=token, error="The two passwords don't match.")
        set_client_password(cid, password)
        log_in_client(cid)
        rotate_csrf_token()
        logger.info(f"Password reset completed for client {cid[:8]}")
        return redirect(url_for("client_portal"))
    return render_template("reset_password.html", token=token, error=None)


@app.route("/client/logout")
def client_logout():
    log_out_client()
    return redirect(url_for("client_login"))


@app.route("/portal")
@client_login_required
def client_portal():
    client = get_client(current_client_id())
    if not client:
        log_out_client()
        return redirect(url_for("client_login"))
    return render_template(
        "portal.html",
        client=client,
        embed_snippet=_embed_snippet(client["api_key"]),
        plans=SELF_SERVE_PLANS,
        verticals=VERTICALS,
        billing_flash=request.args.get("billing", ""),
    )


# Portal API — client_id comes ONLY from the session (current_client_id()),
# never a request parameter, so a logged-in client can reach only its own data.
_KB_TITLE_MAX = 200
_KB_CONTENT_MIN = 10
_KB_CONTENT_MAX = 20000
_KB_CATEGORY_MAX = 50
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_SETTINGS_MAX = {"business_name": 120, "bot_name": 60, "welcome_message": 300}


def _validate_kb_payload(data):
    """Shared validation for KB create/update. Returns (fields, None) or (None, error_response)."""
    title = sanitize_input((data.get("title") or "")).strip()
    content = (data.get("content") or "").strip()
    category = (sanitize_input((data.get("category") or "general")).strip() or "general")[:_KB_CATEGORY_MAX]
    if not title or not content:
        return None, (jsonify({"error": "Title and content are required"}), 400)
    if len(title) > _KB_TITLE_MAX:
        return None, (jsonify({"error": f"Title too long (max {_KB_TITLE_MAX} characters)"}), 400)
    if len(content) < _KB_CONTENT_MIN:
        return None, (jsonify({"error": "Content too short"}), 400)
    if len(content) > _KB_CONTENT_MAX:
        return None, (jsonify({"error": f"Content too long (max {_KB_CONTENT_MAX} characters)"}), 400)
    return {"title": title, "content": content, "category": category}, None


_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_MAX_DOMAINS = 20


def _parse_allowed_domains(raw):
    """Normalize (lowercase, strip scheme/path/trailing dots, dedupe) and validate.
    Returns (domains, None) or (None, error_response). Rejects malformed entries
    and more than _MAX_DOMAINS instead of silently discarding them."""
    items = raw.split(",") if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    out = []
    for d in items:
        d = str(d).strip().lower()
        if not d:
            continue
        if "://" in d:
            d = d.split("://", 1)[1]
        d = d.split("/", 1)[0].split("?", 1)[0].strip(".")
        if not d:
            continue
        if d != "localhost" and not _DOMAIN_RE.match(d):
            return None, (jsonify({"error": f"Invalid domain: {d}"}), 400)
        if d not in out:
            out.append(d)
    if len(out) > _MAX_DOMAINS:
        return None, (jsonify({"error": f"Maximum {_MAX_DOMAINS} allowed domains"}), 400)
    return out, None


@app.route("/api/portal/kb", methods=["GET"])
@client_login_required
def portal_kb_list():
    cid = current_client_id()
    return jsonify({"articles": get_all_articles(cid), "categories": get_categories(cid)})


@app.route("/api/portal/kb", methods=["POST"])
@client_login_required
def portal_kb_create():
    cid = current_client_id()
    fields, err = _validate_kb_payload(request.get_json() or {})
    if err:
        return err
    article = add_article(cid, fields["title"], fields["content"], fields["category"])
    return jsonify({"article": article}), 201


@app.route("/api/portal/kb/<article_id>", methods=["PUT"])
@client_login_required
def portal_kb_update(article_id):
    cid = current_client_id()
    fields, err = _validate_kb_payload(request.get_json() or {})
    if err:
        return err
    article = update_article(cid, article_id, fields["title"], fields["content"], fields["category"])
    if not article:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"article": article})


@app.route("/api/portal/kb/<article_id>", methods=["DELETE"])
@client_login_required
def portal_kb_delete(article_id):
    cid = current_client_id()
    if not delete_article(cid, article_id):
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"status": "deleted"})


@app.route("/api/portal/insights", methods=["GET"])
@client_login_required
def portal_insights():
    from core.insights import build_insights
    client = get_client(current_client_id())
    if not client:
        return jsonify({"error": "Client not found"}), 404
    days = request.args.get("days", 30, type=int)
    return jsonify(build_insights(client["client_id"], client["lead_config"], days))


@app.route("/api/portal/analytics", methods=["GET"])
@client_login_required
def portal_analytics():
    return jsonify(leasing_metrics(current_client_id()))


@app.route("/api/portal/settings", methods=["PUT"])
@client_login_required
def portal_settings():
    """Update the logged-in client's own widget settings. Only a safe subset of
    fields is editable here (never tier, email, api_key, or limits)."""
    cid = current_client_id()
    data = request.get_json() or {}
    updates = {}
    for field, maxlen in _SETTINGS_MAX.items():
        if field in data:
            v = sanitize_input(data[field]).strip()
            if len(v) > maxlen:
                return jsonify({"error": f"{field} too long (max {maxlen} characters)"}), 400
            updates[field] = v
    if "primary_color" in data:
        color = (data.get("primary_color") or "").strip()
        if not _HEX_COLOR.match(color):
            return jsonify({"error": "primary_color must be a hex value like #f59e0b"}), 400
        updates["primary_color"] = color
    if "allowed_domains" in data:
        domains, derr = _parse_allowed_domains(data["allowed_domains"])
        if derr:
            return derr
        updates["allowed_domains"] = domains
    client = update_client(cid, **updates)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"client": client})
 
# â”€â”€ ADMIN HUB â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 
@app.route("/api/portal/leasing", methods=["GET"])
@client_login_required
def portal_leasing_get():
    """Leasing assistant settings plus the question catalog for each rental type."""
    client = get_client(current_client_id())
    if not client:
        return jsonify({"error": "Client not found"}), 404
    catalog = {
        v: [{"key": k, "label": QUESTION_CATALOG[k]["label"], "text": QUESTION_CATALOG[k]["text"],
             "needs_criteria": bool(QUESTION_CATALOG[k].get("needs_criteria"))}
            for k in available_questions(v)]
        for v in RENTAL_VERTICALS
    }
    return jsonify({
        "vertical": client["vertical"],
        "verticals": VERTICALS,
        "config": client["lead_config"],
        "catalog": catalog,
        "account_email": client["email"],
    })


@app.route("/api/portal/leasing", methods=["PUT"])
@client_login_required
def portal_leasing_put():
    cid = current_client_id()
    data = request.get_json() or {}
    vertical = (data.get("vertical") or "").strip()
    if vertical not in VERTICALS:
        return jsonify({"error": "Unknown business type"}), 400
    try:
        config = normalize_lead_config(data.get("config") or {}, vertical, strict=True)
    except LeadConfigError as e:
        return jsonify({"error": str(e)}), 400
    updates = {"vertical": vertical, "lead_config": config}
    client = update_client(cid, **updates)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"vertical": client["vertical"], "config": client["lead_config"]})


@app.route("/api/portal/leads", methods=["GET"])
@client_login_required
def portal_leads():
    cid = current_client_id()
    client = get_client(cid)
    return jsonify({"leads": list_leads(cid, client["lead_config"] if client else None)})


@app.route("/api/portal/leads/<lead_id>", methods=["PUT"])
@client_login_required
def portal_lead_update(lead_id):
    status = ((request.get_json() or {}).get("owner_status") or "").strip()
    if status not in OWNER_STATUSES:
        return jsonify({"error": "owner_status must be one of: " + ", ".join(OWNER_STATUSES)}), 400
    lead = set_owner_status(current_client_id(), lead_id, status)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify({"lead": lead})


@app.route("/api/portal/appointments", methods=["GET"])
@client_login_required
def portal_appointments():
    client = get_client(current_client_id())
    if not client:
        return jsonify({"error": "Client not found"}), 404
    sched = client["lead_config"].get("schedule") or scheduling.normalize_schedule(None)
    return jsonify({
        "appointments": scheduling.list_appointments(client["client_id"], sched),
        "timezone_label": scheduling.tz_label(sched),
        "enabled": scheduling.is_enabled(sched),
    })


@app.route("/api/portal/appointments/<appt_id>/cancel", methods=["POST"])
@client_login_required
def portal_appointment_cancel(appt_id):
    appt = scheduling.cancel(appt_id, client_id=current_client_id(), by="owner")
    if not appt:
        return jsonify({"error": "Appointment not found or already canceled"}), 404
    on_appointment_canceled(appt, by="owner")
    return jsonify({"status": "canceled"})


# Public appointment page (link in the prospect's confirmation email). The
# unguessable token is the credential; it only allows viewing and canceling.
def _appt_context(appt):
    client = get_client(appt.client_id)
    sched = (client or {}).get("lead_config", {}).get("schedule") or scheduling.normalize_schedule(None)
    return client, sched


# The public appointment page speaks the language the prospect chatted in.
_APPT_PAGE = {
    "en": {"html_lang": "en", "title": "Your appointment", "heading": "Your appointment", "confirmed": "Confirmed",
           "canceled": "Canceled", "done": "Your appointment is canceled. {biz} has been notified.", "the_team": "The team",
           "cancel_btn": "Cancel appointment", "confirm": "Cancel this appointment?",
           "reschedule": "To reschedule, cancel here and pick a new time in the chat on our website, or reply to your confirmation email.",
           "not_found": "We couldn't find that appointment.", "expired": "Your session expired. Please reload the page and try again.",
           "started": "This appointment has already started.", "generic": "Appointment"},
    "es": {"html_lang": "es", "title": "Su cita", "heading": "Su cita", "confirmed": "Confirmada",
           "canceled": "Cancelada", "done": "Su cita quedó cancelada. Le avisamos a {biz}.", "the_team": "nuestro equipo",
           "cancel_btn": "Cancelar cita", "confirm": "¿Cancelar esta cita?",
           "reschedule": "Para cambiar la hora, cancele aquí y elija un nuevo horario en el chat de nuestro sitio web, o responda a su correo de confirmación.",
           "not_found": "No encontramos esa cita.", "expired": "Su sesión expiró. Vuelva a cargar la página e inténtelo de nuevo.",
           "started": "Esta cita ya comenzó.", "generic": "Cita"},
}


def _appt_page(record, status=200, **kw):
    """Render the public page; `record` is the Appointment row (or None)."""
    lang = _lead_lang(record.client_id, record.lead_id) if record is not None else "en"
    tx = _APPT_PAGE.get(lang, _APPT_PAGE["en"])
    if kw.get("error_key"):
        kw["error"] = tx[kw.pop("error_key")]
    kw.setdefault("error", None)
    return render_template("appointment.html", tx=tx, lang=lang, **kw), status


@app.route("/appt/<token>", methods=["GET"])
def public_appointment(token):
    if not rate_limit.hit(f"appt:{request.remote_addr}", limit=60, window_seconds=600):
        return "Too many requests", 429
    appt = scheduling.get_by_token(token)
    if not appt:
        return _appt_page(None, 404, appt=None, error_key="not_found")
    client, sched = _appt_context(appt)
    upcoming = scheduling._aware(appt.start_at) > scheduling.now_utc()
    return _appt_page(appt, appt=scheduling.appt_to_dict(appt, sched),
                      business=(client or {}).get("business_name", ""), details=sched.get("details", ""),
                      can_cancel=appt.status == "booked" and upcoming, token=token, done=False)


@app.route("/appt/<token>/cancel", methods=["POST"])
def public_appointment_cancel(token):
    if not rate_limit.hit(f"appt:{request.remote_addr}", limit=60, window_seconds=600):
        return "Too many requests", 429
    existing = scheduling.get_by_token(token)
    if not csrf_valid():
        return _appt_page(existing, 403, appt=None, error_key="expired")
    if not existing:
        return _appt_page(None, 404, appt=None, error_key="not_found")
    client, sched = _appt_context(existing)
    if scheduling._aware(existing.start_at) <= scheduling.now_utc():
        return _appt_page(existing, 400, appt=scheduling.appt_to_dict(existing, sched),
                          business=(client or {}).get("business_name", ""), details="",
                          can_cancel=False, token=token, error_key="started", done=False)
    appt = scheduling.cancel(token=token, by="prospect")
    if appt:
        on_appointment_canceled(appt, by="prospect")
    shown = appt or existing
    return _appt_page(existing, appt=scheduling.appt_to_dict(shown, sched),
                      business=(client or {}).get("business_name", ""), details="",
                      can_cancel=False, token=token, done=True)


@app.route("/api/portal/leads.csv", methods=["GET"])
@client_login_required
def portal_leads_csv():
    body = leads_csv(current_client_id())
    return app.response_class(
        body, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )

 
@app.route("/admin")
@login_required
def admin_hub():
    return render_template("hub.html")
 
@app.route("/admin/kb")
@login_required
def admin_kb():
    return render_template("admin.html")
 
@app.route("/admin/clients")
@login_required
def admin_clients():
    return render_template("clients.html")
 
@app.route("/admin/analytics")
@login_required
def admin_analytics():
    return render_template("analytics.html")
 
# â”€â”€ KNOWLEDGE BASE ROUTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 
def _resolve_admin_client_id(source):
    """Resolve and validate the target client_id for an admin KB operation.

    `source` is request.args (GET/DELETE) or the parsed JSON body (POST/PUT).
    The admin operator manages every tenant's KB, so any existing client is
    allowed; we reject only a missing or unknown client_id, so a write can
    never land on a nonexistent tenant. Returns (client_id, None) on success,
    or (None, (response, status)) on failure.
    """
    cid = (source.get("client_id") or "").strip()
    if not cid:
        return None, (jsonify({"error": "client_id is required"}), 400)
    db = SessionLocal()
    try:
        exists = db.query(Client.id).filter(Client.id == cid).first() is not None
    finally:
        db.close()
    if not exists:
        return None, (jsonify({"error": "Unknown client_id"}), 404)
    return cid, None


@app.route("/api/kb", methods=["GET"])
@api_login_required
def kb_list():
    client_id, err = _resolve_admin_client_id(request.args)
    if err:
        return err
    return jsonify({
        "articles": get_all_articles(client_id),
        "categories": get_categories(client_id),
    })
 
@app.route("/api/kb", methods=["POST"])
@api_login_required
def kb_create():
    data = request.get_json() or {}
    client_id, err = _resolve_admin_client_id(data)
    if err:
        return err
    if not data or not data.get("title") or not data.get("content"):
        return jsonify({"error": "Title and content are required"}), 400
 
    title = sanitize_input(data["title"])
    content = data["content"].strip()
    category = sanitize_input(data.get("category", "general"))
 
    if len(content) < 10:
        return jsonify({"error": "Content too short"}), 400
 
    article = add_article(client_id, title, content, category)
    logger.info(f"KB article added for client {client_id[:8]}: {title}")
    return jsonify({"article": article}), 201
 
@app.route("/api/kb/<article_id>", methods=["GET"])
@api_login_required
def kb_get(article_id):
    client_id, err = _resolve_admin_client_id(request.args)
    if err:
        return err
    article = get_article(client_id, article_id)
    if not article:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"article": article})
 
@app.route("/api/kb/<article_id>", methods=["PUT"])
@api_login_required
def kb_update(article_id):
    data = request.get_json() or {}
    client_id, err = _resolve_admin_client_id(data)
    if err:
        return err
    if not data or not data.get("title") or not data.get("content"):
        return jsonify({"error": "Title and content are required"}), 400
 
    article = update_article(
        client_id,
        article_id,
        sanitize_input(data["title"]),
        data["content"].strip(),
        sanitize_input(data.get("category", "general"))
    )
 
    if not article:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"article": article})
 
@app.route("/api/kb/<article_id>", methods=["DELETE"])
@api_login_required
def kb_delete(article_id):
    client_id, err = _resolve_admin_client_id(request.args)
    if err:
        return err
    success = delete_article(client_id, article_id)
    if not success:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"status": "deleted"})
 
def _tier_error(tier, client_id=None):
    """Operator-set tiers must be real tiers. "unlimited" is for operator-run
    pilot accounts only, never a self-serve (trial or Stripe) account."""
    if tier not in TIER_LIMITS:
        return jsonify({"error": f"Unknown tier. Use one of: {', '.join(TIER_LIMITS)}"}), 400
    if tier == "unlimited" and client_id:
        db = SessionLocal()
        try:
            c = db.get(Client, client_id)
            self_serve = bool(c and (c.password_hash or c.trial_ends_at or c.stripe_subscription_id))
        finally:
            db.close()
        if self_serve:
            return jsonify({"error": "Unlimited is only for operator-managed accounts, not self-serve ones."}), 400
    return None


# â”€â”€ CLIENT MANAGEMENT ROUTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 
@app.route("/api/clients", methods=["GET"])
@api_login_required
def clients_list():
    clients = get_all_clients()
    stats = get_client_stats()
    return jsonify({"clients": clients, "stats": stats})
 
@app.route("/api/clients", methods=["POST"])
@api_login_required
def clients_create():
    data = request.get_json()
    if not data or not data.get("business_name") or not data.get("email"):
        return jsonify({"error": "Business name and email are required"}), 400
    err = _tier_error(data.get("tier", "starter"))
    if err:
        return err
 
    client = create_client(
        business_name=sanitize_input(data["business_name"]),
        email=sanitize_input(data["email"]),
        tier=data.get("tier", "starter"),
        bot_name=sanitize_input(data.get("bot_name", "Leasing Assistant")),
        api_key=data.get("api_key", ""),
        billing_date=data.get("billing_date", "")
    )
 
    if data.get("notes"):
        update_client(client["client_id"], notes=sanitize_input(data["notes"]))
 
    logger.info(f"New client: {client['business_name']} ({client['tier']})")
    return jsonify({"client": client}), 201
 
@app.route("/api/clients/<client_id>", methods=["PUT"])
@api_login_required
def clients_update(client_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
 
    updates = {}
    for field in ["business_name", "email", "bot_name", "billing_date", "notes"]:
        if field in data:
            updates[field] = sanitize_input(data[field])
    if "tier" in data:
        err = _tier_error(data["tier"], client_id)
        if err:
            return err
        updates["tier"] = data["tier"]
 
    client = update_client(client_id, **updates)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"client": client})
 
@app.route("/api/clients/<client_id>/suspend", methods=["POST"])
@api_login_required
def clients_suspend(client_id):
    client = suspend_client(client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"client": client})
 
@app.route("/api/clients/<client_id>/activate", methods=["POST"])
@api_login_required
def clients_activate(client_id):
    client = reactivate_client(client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"client": client})
 
@app.route("/api/clients/<client_id>", methods=["DELETE"])
@api_login_required
def clients_delete(client_id):
    # Stop billing first: deleting the row alone would leave Stripe charging
    # a customer who no longer has an account.
    db = SessionLocal()
    try:
        c = db.get(Client, client_id)
        sub_id = c.stripe_subscription_id if c else None
        status = (c.subscription_status or "").lower() if c else ""
    finally:
        db.close()
    if sub_id and status not in ("canceled", "incomplete_expired"):
        try:
            import stripe
            stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
            stripe.Subscription.cancel(sub_id)
        except Exception as e:
            logger.error(f"clients_delete: could not cancel Stripe subscription for {client_id[:8]}: {e}")
            return jsonify({"error": "Could not cancel this client's Stripe subscription, so nothing was deleted. Cancel it in Stripe and try again."}), 502
    success = delete_client(client_id)
    if not success:
        return jsonify({"error": "Client not found"}), 404
    return jsonify({"status": "deleted"})
 
# â”€â”€ ANALYTICS ROUTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 
@app.route("/api/analytics/insights", methods=["GET"])
@api_login_required
def analytics_insights():
    from core.insights import build_insights
    client_id, err = _resolve_admin_client_id(request.args)
    if err:
        return err
    client = get_client(client_id)
    return jsonify(build_insights(client_id, (client or {}).get("lead_config") or {},
                                  request.args.get("days", 30, type=int)))


@app.route("/api/analytics", methods=["GET"])
@api_login_required
def analytics_data():
    client_id, err = _resolve_admin_client_id(request.args)
    if err:
        return err
    return jsonify(leasing_metrics(client_id))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)



