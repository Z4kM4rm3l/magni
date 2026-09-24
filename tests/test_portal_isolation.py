"""Standalone portal isolation + validation + CSRF tests (Flask test client on an
isolated SQLite DB, no Postgres, no Gemini). Run: python tests/test_portal_isolation.py
"""
import os, sys, types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")

# Stub Gemini.
_g = types.ModuleType("google"); _ga = types.ModuleType("google.generativeai")
_ga.configure = lambda **k: None
class _StubModel:
    def __init__(self, *a, **k): pass
    def start_chat(self, *a, **k): return types.SimpleNamespace(send_message=lambda *a, **k: types.SimpleNamespace(text="ok"))
    def generate_content(self, *a, **k): return types.SimpleNamespace(text="stub summary")
_ga.GenerativeModel = _StubModel
sys.modules["google"] = _g; sys.modules["google.generativeai"] = _ga

# Isolated SQLite bound into core.db; neutralize init_db so importing app does not run Alembic.
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import core.db as _db
_db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db.engine)
_db.init_db = lambda: None

import core.models as models
models.Base.metadata.create_all(bind=_db.engine)

import app as appmod

TOKEN = "test-csrf-token"

class CsrfClient:
    """Mirrors the browser's fetch wrapper: attaches X-CSRF-Token to mutating requests."""
    def __init__(self, c): self.c = c
    def _h(self, kw):
        h = dict(kw.pop("headers", None) or {}); h["X-CSRF-Token"] = TOKEN; kw["headers"] = h; return kw
    def get(self, *a, **k): return self.c.get(*a, **k)
    def post(self, *a, **k): return self.c.post(*a, **self._h(k))
    def put(self, *a, **k): return self.c.put(*a, **self._h(k))
    def delete(self, *a, **k): return self.c.delete(*a, **self._h(k))

def set_token(c):
    with c.session_transaction() as s:
        s["_csrf_token"] = TOKEN

def signup(email):
    c = appmod.app.test_client()
    set_token(c)
    c.post("/signup", data={"business_name": email.split("@")[0], "email": email, "password": "Passw0rd123", "csrf_token": TOKEN})
    set_token(c)  # the app rotates the token on login; pin a known one for the test
    return CsrfClient(c)

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

A = signup("a@acme.com")
B = signup("b@beta.com")
anon = appmod.app.test_client()

# --- Auth guard ---
check("logged-out GET /api/portal/kb -> 401", anon.get("/api/portal/kb").status_code == 401)
check("logged-out PUT /api/portal/settings -> 401", anon.put("/api/portal/settings", json={"business_name": "x"}).status_code == 401)

# --- CSRF: authenticated mutations require the token ---
check("logged-in mutation WITHOUT csrf token -> 403", A.c.post("/api/portal/kb", json={"title": "t", "content": "long enough content"}).status_code == 403)
check("logged-in mutation with WRONG csrf token -> 403", A.c.post("/api/portal/kb", json={"title": "t", "content": "long enough content"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403)
check("GET does not require csrf", A.get("/api/portal/kb").status_code == 200)
fresh = appmod.app.test_client()
check("signup POST without csrf -> 403", fresh.post("/signup", data={"business_name": "x", "email": "x@x.com", "password": "Passw0rd123"}).status_code == 403)
check("client login POST without csrf -> 403", fresh.post("/client/login", data={"email": "a@acme.com", "password": "Passw0rd123"}).status_code == 403)

# --- A owns an article; B cannot reach it ---
aid = A.post("/api/portal/kb", json={"title": "Refunds", "content": "We refund within 30 days of purchase.", "category": "billing"}).get_json()["article"]["id"]
check("B GET kb excludes A's article", all(x["id"] != aid for x in B.get("/api/portal/kb").get_json()["articles"]))
check("B PUT A's article -> 404", B.put("/api/portal/kb/" + aid, json={"title": "hax", "content": "malicious overwrite content"}).status_code == 404)
check("B DELETE A's article -> 404", B.delete("/api/portal/kb/" + aid).status_code == 404)
check("A's article survived", any(x["id"] == aid and x["title"] == "Refunds" for x in A.get("/api/portal/kb").get_json()["articles"]))

# --- Invalid article id does not leak / does not 500 ---
check("A PUT nonexistent id -> 404", A.put("/api/portal/kb/nope", json={"title": "t", "content": "long enough content"}).status_code == 404)
check("A DELETE nonexistent id -> 404", A.delete("/api/portal/kb/nope").status_code == 404)

# --- KB validation symmetric (POST and PUT) ---
check("POST content too short -> 400", A.post("/api/portal/kb", json={"title": "t", "content": "short"}).status_code == 400)
check("PUT content too short -> 400", A.put("/api/portal/kb/" + aid, json={"title": "t", "content": "short"}).status_code == 400)
check("malformed/empty body -> 400 (no 500)", A.post("/api/portal/kb", json={}).status_code == 400)
check("overlong KB title -> 400", A.post("/api/portal/kb", json={"title": "x" * 300, "content": "long enough content here"}).status_code == 400)
check("overlong KB content -> 400", A.post("/api/portal/kb", json={"title": "ok", "content": "y" * 20001}).status_code == 400)
check("payload over 1MB -> 413", A.post("/api/portal/kb", json={"title": "big", "content": "z" * 1_100_000}).status_code == 413)

# --- A can manage its OWN article (positive control) ---
own = A.post("/api/portal/kb", json={"title": "Hours", "content": "Open 9 to 5 on weekdays."}).get_json()["article"]["id"]
check("A can PUT its own article", A.put("/api/portal/kb/" + own, json={"title": "Hours (updated)", "content": "Open 9 to 6 on weekdays."}).status_code == 200)
check("A can DELETE its own article", A.delete("/api/portal/kb/" + own).status_code == 200)

# --- Settings whitelist: restricted fields cannot be changed ---
s = _db.SessionLocal(); a_row = s.query(models.Client).filter(models.Client.email == "a@acme.com").first()
a_id, a_tier, a_key, a_email = a_row.id, a_row.tier, a_row.api_key, a_row.email
s.close()
A.put("/api/portal/settings", json={
    "business_name": "Acme Renamed", "bot_name": "Ace", "welcome_message": "Hi!",
    "primary_color": "#123456", "allowed_domains": "ACME.com, https://www.acme.com/path",
    "tier": "enterprise", "email": "evil@x.com", "api_key": "sk_hacked", "monthly_limit": 999999,
})
s = _db.SessionLocal(); a_row = s.query(models.Client).filter(models.Client.id == a_id).first()
check("settings updated business_name", a_row.business_name == "Acme Renamed")
check("settings did NOT change tier", a_row.tier == a_tier)
check("settings did NOT change api_key", a_row.api_key == a_key)
check("settings did NOT change email", a_row.email == a_email)
check("allowed_domains normalized (lowercased, scheme/path stripped, deduped)", a_row.allowed_domains == ["acme.com", "www.acme.com"])
s.close()

# --- Settings validation ---
check("bad hex color -> 400", A.put("/api/portal/settings", json={"primary_color": "red"}).status_code == 400)
check("overlong business_name -> 400", A.put("/api/portal/settings", json={"business_name": "x" * 200}).status_code == 400)
check("invalid domain 'hello world' -> 400", A.put("/api/portal/settings", json={"allowed_domains": "hello world"}).status_code == 400)
check("more than 20 domains -> 400 (not silently truncated)", A.put("/api/portal/settings", json={"allowed_domains": ",".join(f"d{i}.com" for i in range(21))}).status_code == 400)
check("exactly 20 domains ok", A.put("/api/portal/settings", json={"allowed_domains": ",".join(f"d{i}.com" for i in range(20))}).status_code == 200)

# --- B changing its own settings does not affect A (no client-id input) ---
s = _db.SessionLocal(); a_before = s.query(models.Client).filter(models.Client.id == a_id).first().business_name; s.close()
B.put("/api/portal/settings", json={"business_name": "Beta Renamed"})
s = _db.SessionLocal(); a_after = s.query(models.Client).filter(models.Client.id == a_id).first().business_name; s.close()
check("B's settings save does not touch A", a_after == a_before)

# --- Suspended client with an existing session is forced out on next request ---
C = signup("c@gamma.com")
check("C can access portal while active", C.get("/api/portal/kb").status_code == 200)
s = _db.SessionLocal(); c_row = s.query(models.Client).filter(models.Client.email == "c@gamma.com").first(); c_row.is_active = False; s.commit(); s.close()
check("C is forced out after suspension (next request 401)", C.get("/api/portal/kb").status_code == 401)

# --- Suspended client cannot log in ---
s = _db.SessionLocal(); b_row = s.query(models.Client).filter(models.Client.email == "b@beta.com").first(); b_row.is_active = False; s.commit(); s.close()
lc = appmod.app.test_client(); set_token(lc)
r = lc.post("/client/login", data={"email": "b@beta.com", "password": "Passw0rd123", "csrf_token": TOKEN})
check("suspended client login is rejected (no redirect to portal)", r.status_code == 200 and "/portal" not in r.headers.get("Location", ""))


# --- API-key resolver: active clients resolve, suspended clients do not ---
D = signup("d@delta.com")
s = _db.SessionLocal()
d_row = s.query(models.Client).filter(models.Client.email == "d@delta.com").first()
d_id, d_key = d_row.id, d_row.api_key
s.close()

from core.client_manager import get_client_by_api_key

resolved = get_client_by_api_key(d_key, active_only=True)
check("active API key resolves through shared resolver", resolved is not None and resolved.id == d_id)
with appmod.app.test_request_context("/", headers={"X-Magni-API-Key": d_key}):
    check("active API key resolves through public request resolver", appmod._caller_client_id() == d_id)

s = _db.SessionLocal()
s.query(models.Client).filter(models.Client.id == d_id).first().is_active = False
s.commit(); s.close()

check("suspended API key rejected by shared resolver", get_client_by_api_key(d_key, active_only=True) is None)
with appmod.app.test_request_context("/", headers={"X-Magni-API-Key": d_key}):
    check("suspended API key rejected by public request resolver", appmod._caller_client_id() is None)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}"); sys.exit(1)
print("ALL PASSED"); sys.exit(0)
