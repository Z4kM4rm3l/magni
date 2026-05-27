"""
core/security.py
Centralized security middleware for Magni.
Call apply_security_headers(app) once in app.py after creating the Flask app.
"""
import os
from flask import Flask, request, jsonify
from core.utils import logger


def apply_security_headers(app: Flask):
    """
    Attach security headers to every response and configure
    CORS, content-type enforcement, and clickjacking protection.
    """

    # ── Allowed origins for CORS ─────────────────────────────────────
    ALLOWED_ORIGINS = [
        o.strip() for o in
        os.getenv("ALLOWED_ORIGINS", "https://web-production-7362f.up.railway.app").split(",")
        if o.strip()
    ]

    @app.after_request
    def add_security_headers(response):
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Basic XSS protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Don't leak referrer to third parties
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy — tight but functional
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "   # inline JS needed for our SVG avatars
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["Content-Security-Policy"] = csp

        # HTTPS enforcement (Railway always serves HTTPS)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # CORS — restrict to known origins
        origin = request.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization, X-Magni-API-Key"
            )
        elif not origin:
            # Same-origin requests (no Origin header) always allowed
            pass

        return response

    @app.before_request
    def handle_preflight():
        """Handle CORS preflight OPTIONS requests."""
        if request.method == "OPTIONS":
            response = app.make_default_options_response()
            origin = request.headers.get("Origin", "")
            if origin in ALLOWED_ORIGINS:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = (
                    "Content-Type, Authorization, X-Magni-API-Key"
                )
                response.headers["Access-Control-Max-Age"] = "86400"
            return response

    @app.before_request
    def enforce_content_type():
        """Require JSON content-type on POST/PUT API requests."""
        if request.method in ("POST", "PUT") and request.path.startswith("/api/"):
            ct = request.content_type or ""
            if "application/json" not in ct and request.path != "/api/v1/stripe-webhook":
                return jsonify({"error": "Content-Type must be application/json"}), 415

    logger.info("Security middleware applied.")
