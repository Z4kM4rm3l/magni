import os
import functools
from flask import session, redirect, url_for, request
from dotenv import load_dotenv

load_dotenv()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "lemram")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")

def login_required(f):
    """Decorator to protect admin routes."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def check_credentials(username: str, password: str) -> bool:
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD
