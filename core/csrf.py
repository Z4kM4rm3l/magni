"""
core/csrf.py

Session-bound CSRF protection for session-authenticated mutations.

A per-session token is generated lazily and exposed to templates as
`csrf_token` (rendered into a <meta> tag and hidden form fields). Mutating
requests (POST/PUT/PATCH/DELETE) on session-authenticated routes must echo it
back via the X-CSRF-Token header (AJAX) or a csrf_token form field (forms).

Public surfaces (the embeddable widget, /chat, /resolve, /feedback, Stripe
webhooks) authenticate by API key or signature, not session cookies, and are
intentionally not covered.
"""
import secrets
from flask import session, request

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_csrf_token() -> str:
    tok = session.get("_csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf_token"] = tok
    return tok


def rotate_csrf_token() -> str:
    """Issue a fresh token (call on login to avoid token fixation across sessions)."""
    tok = secrets.token_urlsafe(32)
    session["_csrf_token"] = tok
    return tok


def _provided_token() -> str | None:
    tok = request.headers.get("X-CSRF-Token")
    if tok:
        return tok
    if request.form:
        tok = request.form.get("csrf_token")
        if tok:
            return tok
    if request.is_json:
        body = request.get_json(silent=True) or {}
        return body.get("csrf_token")
    return None


def csrf_valid() -> bool:
    expected = session.get("_csrf_token")
    provided = _provided_token()
    return bool(expected) and bool(provided) and secrets.compare_digest(expected, provided)


def csrf_required_for_request() -> bool:
    return request.method.upper() in MUTATING_METHODS
