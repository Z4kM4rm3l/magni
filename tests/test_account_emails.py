"""Account emails: the SMTP provider is configurable, escalation alerts share
the one sender, forgot-password links are single-use and expire, and owners
get one warning at 80% of their conversations and one at the limit.
Run: python tests/test_account_emails.py
"""
import os, sys, re
from markupsafe import escape as _esc
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")

import core.llm as _llm
_llm.generate = lambda *a, **k: '{"intent": "general", "confidence": 0.9, "route": "resolver", "escalation_reason": null}'
_llm.chat = lambda *a, **k: "Happy to help."

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
from core import notifier, rate_limit, client_auth
from core.models import Client

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

# --- SMTP provider settings ---
import smtplib
CONNS = []
class FakeSMTP:
    def __init__(self, host, port, timeout=None): self.host, self.port = host, port; self.login_user = None; CONNS.append(self)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, user, pw): self.login_user = user
    def sendmail(self, frm, to, msg): self.frm, self.to, self.msg = frm, to, msg
smtplib.SMTP = FakeSMTP
os.environ.update(MAGNI_SMTP_FROM="alerts@palm.com", MAGNI_SMTP_PASSWORD="tok",
                  MAGNI_SMTP_HOST="smtp.postmarkapp.com", MAGNI_SMTP_PORT="2525", MAGNI_SMTP_USER="server-token")
check("email goes through the configured provider", notifier.send_email("a@b.com", "Hi", "Body")
      and CONNS[-1].host == "smtp.postmarkapp.com" and CONNS[-1].port == 2525 and CONNS[-1].login_user == "server-token")
for k in ("MAGNI_SMTP_HOST", "MAGNI_SMTP_PORT", "MAGNI_SMTP_USER"):
    os.environ.pop(k)
notifier.send_email("a@b.com", "Hi", "Body")
check("unset provider falls back to Gmail, logging in as the from address",
      CONNS[-1].host == "smtp.gmail.com" and CONNS[-1].port == 587 and CONNS[-1].login_user == "alerts@palm.com")
from core.emailer import send_escalation_alert
send_escalation_alert("o@palm.com", "Palm Props", "human_handoff", "asked for a person", [{"role": "user", "content": "hi"}], "abcdef123")
check("escalation alerts use the same sender", CONNS[-1].to == ["o@palm.com"] and "Needs a person" in CONNS[-1].msg)

# From here on, record emails instead of sending.
notifier.RUN_IN_BACKGROUND = False
SENT = []
notifier.send_email = lambda to, subj, body, reply_to=None, **kw: SENT.append((to, subj, body)) or True

# --- Forgot password ---
TOKEN = "t"
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["_csrf_token"] = TOKEN
    return c
c = client()
c.post("/signup", data={"business_name": "Palm Props", "email": "o@palm.com", "password": "OldPassw0rd", "csrf_token": TOKEN})

check("login page links to forgot password", "/client/forgot" in client().get("/client/login").get_data(as_text=True))
rate_limit.reset()
r = client().post("/client/forgot", data={"email": "nobody@x.com", "csrf_token": TOKEN})
same_msg = _esc(appmod._RESET_SENT) in r.get_data(as_text=True)
check("unknown email gets the same answer and no email", same_msg and SENT == [])
r = client().post("/client/forgot", data={"email": "O@Palm.com", "csrf_token": TOKEN})
check("known email gets the same answer", _esc(appmod._RESET_SENT) in r.get_data(as_text=True))
check("known email gets a reset link", len(SENT) == 1 and SENT[0][0] == "o@palm.com" and "/client/reset/" in SENT[0][2])
link = re.search(r"https://app\.example\.com(/client/reset/\S+)", SENT[0][2]).group(1)
check("reset link uses PUBLIC_BASE_URL", SENT[0][2].count("https://app.example.com/client/reset/") == 1)
check("forgot form requires CSRF", appmod.app.test_client().post("/client/forgot", data={"email": "o@palm.com"}).status_code == 403)

rc = client()
check("reset page opens", rc.get(link).status_code == 200)
r = rc.post(link, data={"password": "NewPassw0rd", "confirm": "Different1", "csrf_token": TOKEN})
check("mismatched passwords are refused", _esc("don't match") in r.get_data(as_text=True))
r = rc.post(link, data={"password": "short", "confirm": "short", "csrf_token": TOKEN})
check("short password is refused", "at least 8" in r.get_data(as_text=True))
r = rc.post(link, data={"password": "NewPassw0rd", "confirm": "NewPassw0rd", "csrf_token": TOKEN})
check("valid reset logs the owner in", r.status_code in (302, 303) and "/portal" in r.headers.get("Location", ""))
check("new password works", client_auth.authenticate_client("o@palm.com", "NewPassw0rd") is not None)
check("old password no longer works", client_auth.authenticate_client("o@palm.com", "OldPassw0rd") is None)
check("the same link can't be used twice", client().get(link).status_code == 400)
check("a tampered link is refused", client().get(link[:-3] + "xyz").status_code == 400)
fresh = client_auth.make_reset_token("o@palm.com")
client_auth.RESET_MAX_AGE = -1
check("an expired link is refused", client_auth.check_reset_token(fresh) is None)
client_auth.RESET_MAX_AGE = 3600
d = _db.SessionLocal(); d.query(Client).filter(Client.email == "o@palm.com").first().is_active = False; d.commit(); d.close()
check("a suspended account can't reset", client_auth.check_reset_token(fresh) is None)
d = _db.SessionLocal(); d.query(Client).filter(Client.email == "o@palm.com").first().is_active = True; d.commit(); d.close()

rate_limit.reset()
SENT.clear()
codes = [client().post("/client/forgot", data={"email": "o@palm.com", "csrf_token": TOKEN}).status_code for _ in range(4)]
check("forgot-password is rate limited per email", codes[:3] == [200, 200, 200] and codes[3] == 429 and len(SENT) == 3)

# --- Usage warnings at 80% and at the limit ---
d = _db.SessionLocal()
d.add(Client(id="c-small", api_key="k-small", business_name="Bay Rentals", email="owner@bay.com",
             tier="starter", is_active=True, monthly_used=0, monthly_limit=5))
d.add(Client(id="c-unl", api_key="k-unl", business_name="Pilot", email="p@pilot.com",
             tier="unlimited", is_active=True, monthly_used=0, monthly_limit=-1))
d.commit(); d.close()
w = appmod.app.test_client()
def new_chat(key, sid=None):
    rate_limit.reset()
    body = {"message": "hi"} if not sid else {"message": "hi again", "session_id": sid}
    return (w.post("/widget/chat", json=body, headers={"X-Magni-API-Key": key}).get_json() or {}).get("session_id")
SENT.clear()
sids = [new_chat("k-small") for _ in range(3)]
check("no warning below 80%", SENT == [])
new_chat("k-small")
check("one warning at 80% (4 of 5)", len(SENT) == 1 and "80%" in SENT[0][1] and SENT[0][0] == "owner@bay.com" and "4 of the 5" in SENT[0][2])
new_chat("k-small", sids[0])
check("continuing a chat sends nothing", len(SENT) == 1)
new_chat("k-small")
check("one warning at the limit (5 of 5)", len(SENT) == 2 and "reached" in SENT[1][1] and "/portal#billing" in SENT[1][2])
new_chat("k-small")
check("refused chats past the limit send no more warnings", len(SENT) == 2)
for _ in range(3):
    new_chat("k-unl")
check("unlimited accounts never get warnings", len(SENT) == 2)

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASSED")
