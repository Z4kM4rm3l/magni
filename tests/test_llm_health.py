"""Operator alert when model calls start failing: threshold, cooldown, recovery
notice, targets from the environment, and the hook in core.llm._call.
Run: python tests/test_llm_health.py
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ["OPERATOR_ALERT_EMAIL"] = "ops@example.com"
os.environ["OPERATOR_ALERT_PHONE"] = "(239) 555-0100"

from core import notifier, llm_health as H, llm

notifier.RUN_IN_BACKGROUND = False
SENT = {"email": [], "sms": []}
notifier.send_email = lambda to, subj, body, reply_to=None, **kw: SENT["email"].append((to, subj, body)) or True
notifier.send_sms = lambda to, body: SENT["sms"].append((to, body)) or True

CLOCK = {"t": 1_000_000.0}
H.time.time = lambda: CLOCK["t"]

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

def fresh():
    H.reset(); SENT["email"].clear(); SENT["sms"].clear()

fresh()
H.record_failure(RuntimeError("overloaded_error 529"))
H.record_failure(RuntimeError("overloaded_error 529"))
check("one or two failures stay quiet", not SENT["email"] and not SENT["sms"])
H.record_failure(RuntimeError("overloaded_error 529"))
check("the third failure in the window alerts by email", len(SENT["email"]) == 1 and SENT["email"][0][0] == "ops@example.com")
check("and by text, to the normalized number", len(SENT["sms"]) == 1 and SENT["sms"][0][0] == "+12395550100")
subj, body = SENT["email"][0][1], SENT["email"][0][2]
check("the alert says what failed and what to check",
      subj == "Magni: AI model calls are failing" and "overloaded_error" in body and "status.claude.com" in body)
check("the alert explains what visitors see", "contact details" in body)

for _ in range(20):
    CLOCK["t"] += 30
    H.record_failure(RuntimeError("overloaded_error 529"))
check("no repeat inside the 30 minute cooldown", len(SENT["email"]) == 1)
CLOCK["t"] += H.COOLDOWN_SECONDS
for _ in range(3):
    H.record_failure(RuntimeError("overloaded_error 529"))
check("still failing after the cooldown: one reminder",
      len(SENT["email"]) == 2 and SENT["email"][1][1] == "Magni: AI model calls are still failing")

H.record_success()
check("recovery sends one notice", len(SENT["email"]) == 3 and "working again" in SENT["email"][2][1] and len(SENT["sms"]) == 3)
H.record_success()
check("and only one", len(SENT["email"]) == 3)

fresh()
H.record_failure("x"); H.record_failure("x")
CLOCK["t"] += H.WINDOW_SECONDS + 1
H.record_failure("x")
check("failures spread out past the window don't alert", not SENT["email"])
fresh()
H.record_failure("x"); H.record_failure("x"); H.record_success(); H.record_failure("x")
check("a success in between clears the count", not SENT["email"])

fresh()
for _ in range(3):
    H.record_failure(RuntimeError("Your credit balance is too low to access the API"))
check("out of credits is called out", "out of credits" in SENT["email"][0][2])
fresh()
for _ in range(3):
    H.record_failure(RuntimeError("401 authentication_error: invalid x-api-key"))
check("a bad key is called out", "ANTHROPIC_API_KEY" in SENT["email"][0][2])

fresh()
os.environ["OPERATOR_ALERT_EMAIL"] = ""; os.environ["OPERATOR_ALERT_PHONE"] = ""
for _ in range(3):
    H.record_failure("x")
check("with no targets set nothing is sent and nothing breaks", not SENT["email"] and not SENT["sms"])
os.environ["OPERATOR_ALERT_EMAIL"] = "ops@example.com"

# ── The hook: every real model call reports in ──────────────────────────────
class _Block:
    type = "text"; text = "hello"
class _Resp:
    stop_reason = "end_turn"; content = [_Block()]
class _Messages:
    def __init__(self): self.fail = True
    def create(self, **kw):
        if self.fail:
            raise RuntimeError("overloaded_error 529")
        return _Resp()
class _Client:
    messages = _Messages()
llm._client = _Client()
fresh()
raised = 0
for _ in range(3):
    try:
        llm.generate("hi")
    except RuntimeError:
        raised += 1
check("failed calls still raise to the caller", raised == 3)
check("three failed real calls alert the operator", len(SENT["email"]) == 1)
_Client.messages.fail = False
check("a working call returns its text", llm.chat("sys", [], "hi") == "hello")
check("and sends the recovery notice", len(SENT["email"]) == 2 and "working again" in SENT["email"][1][1])

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASSED")
