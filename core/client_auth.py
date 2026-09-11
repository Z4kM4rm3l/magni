"""
core/client_auth.py

Client-side authentication for the self-serve portal, kept entirely separate
from the operator admin auth in core/auth.py. Operator and client sessions use
different session keys and never grant each other's access.

- Passwords are hashed with werkzeug (same primitive as the operator auth).
- authenticate_client / set_client_password live here and touch the DB.
- log_in_client / log_out_client manage the client session.
- client_login_required guards portal routes.
"""
import time
from functools import wraps

from flask import session, redirect, url_for, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash

from core.db import SessionLocal
from core.models import Client
from core.utils import logger

CLIENT_SESSION_TTL = 60 * 60 * 12  # 12 hours


# ── Password storage / verification (DB) ────────────────────────────────────
def set_client_password(client_id: str, raw_password: str) -> bool:
    db = SessionLocal()
    try:
        c = db.get(Client, client_id)
        if not c:
            return False
        c.password_hash = generate_password_hash(raw_password)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"set_client_password error (client={str(client_id)[:8]}): {e}")
        raise
    finally:
        db.close()


def authenticate_client(email: str, raw_password: str) -> str | None:
    """Return the client_id if email+password match a client with a password set."""
    db = SessionLocal()
    try:
        c = (
            db.query(Client)
            .filter(Client.email == email, Client.password_hash.isnot(None))
            .first()
        )
        if c and check_password_hash(c.password_hash, raw_password):
            return c.id
        return None
    finally:
        db.close()


def email_exists(email: str) -> bool:
    db = SessionLocal()
    try:
        return db.query(Client.id).filter(Client.email == email).first() is not None
    finally:
        db.close()


# ── Session management ──────────────────────────────────────────────────────
def log_in_client(client_id: str):
    session["client_id"] = client_id
    session["client_login_time"] = time.time()


def log_out_client():
    session.pop("client_id", None)
    session.pop("client_login_time", None)


def is_client_session_valid() -> bool:
    if not session.get("client_id"):
        return False
    started = session.get("client_login_time", 0)
    if time.time() - started > CLIENT_SESSION_TTL:
        log_out_client()
        return False
    return True


def current_client_id() -> str | None:
    return session.get("client_id") if is_client_session_valid() else None


def client_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_client_session_valid():
            # HTML routes redirect to login; API routes get a 401.
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("client_login"))
        return f(*args, **kwargs)
    return decorated
