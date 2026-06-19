"""
Standalone orchestrator test — no Flask, no Gemini calls.
Patches agents with stubs to test routing logic only.
Run: python tests/test_orchestrator.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from agents.orchestrator import Orchestrator, _summarize_history
import agents.triage_agent as _triage_mod
import agents.resolver_agent as _resolver_mod
import agents.escalation_agent as _escalation_mod


# ── Stub agents ──────────────────────────────────────────────────────────────

class StubTriage:
    def __init__(self, route, intent="general"):
        self._route = route
        self._intent = intent
    def run(self, payload):
        return {"intent": self._intent, "confidence": 0.9,
                "route": self._route, "escalation_reason": "stub"}

class StubResolver:
    def __init__(self, escalation_flag=False):
        self._flag = escalation_flag
    def run(self, payload):
        return {"response_text": "Resolver response.", "escalation_flag": self._flag, "confidence": 0.9}

class StubEscalation:
    def __init__(self, action="collect_contact"):
        self._action = action
    def run(self, payload):
        return {"action": self._action, "message_to_user": "Escalation message.", "alert_client": False, "reason": "stub"}


# ── History summarizer unit test ──────────────────────────────────────────────

def test_summarize_history():
    assert _summarize_history([]) == "No prior context."
    history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    summary = _summarize_history(history)
    assert "USER: Hello" in summary
    assert "ASSISTANT: Hi there" in summary
    print("  summarize_history: OK")


# ── Routing logic tests ───────────────────────────────────────────────────────

def make_orch(triage_route, resolver_escalates=False, triage_intent="general"):
    _triage_mod.triage_agent = StubTriage(triage_route, intent=triage_intent)
    _resolver_mod.resolver_agent = StubResolver(escalation_flag=resolver_escalates)
    _escalation_mod.escalation_agent = StubEscalation()
    return Orchestrator()

cases = [
    {
        "label": "triage->resolver route returns response",
        "setup": ("resolver", False, "general"),
        "expect_route": "resolver",
        "expect_response": "Resolver response.",
    },
    {
        "label": "triage->escalation route fires escalation agent",
        "setup": ("escalation", False, "general"),
        "expect_route": "escalation",
        "expect_response": "Escalation message.",
    },
    {
        "label": "resolver sets escalation_flag -> resolver_escalated",
        "setup": ("resolver", True, "general"),
        "expect_route": "resolver_escalated",
        "expect_response": "Resolver response.",
    },
    {
        "label": "intent is threaded through to output",
        "setup": ("resolver", False, "billing"),
        "expect_route": "resolver",
        "expect_intent": "billing",
    },
    {
        "label": "no api_key skips billing gate",
        "setup": ("resolver", False, "general"),
        "api_key": None,
        "expect_route": "resolver",
    },
]

all_passed = True
print("Running orchestrator tests...\n")
test_summarize_history()

for case in cases:
    triage_route, resolver_escalates, triage_intent = case["setup"]
    orch = make_orch(triage_route, resolver_escalates, triage_intent)
    result = orch.run(
        message="test message",
        session_id="test-session-id",
        history=[],
        api_key=case.get("api_key"),  # None by default
    )
    checks = []
    ok = True

    if "expect_route" in case:
        match = result["route"] == case["expect_route"]
        checks.append(f"route={result['route']} ({'OK' if match else 'FAIL expected ' + case['expect_route']})")
        if not match: ok = False

    if "expect_response" in case:
        match = result["response"] == case["expect_response"]
        checks.append(f"response={'OK' if match else 'FAIL: ' + result['response'][:60]}")
        if not match: ok = False

    if "expect_intent" in case:
        match = result["intent"] == case["expect_intent"]
        checks.append(f"intent={result['intent']} ({'OK' if match else 'FAIL expected ' + case['expect_intent']})")
        if not match: ok = False

    if not ok:
        all_passed = False

    print(f"  [{case['label']}] {' | '.join(checks)}")

print()
print("PASS" if all_passed else "FAIL -- see above")
