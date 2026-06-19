"""Standalone test — no Flask required. Run: python tests/test_triage_agent.py"""
import sys, os, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from agents.triage_agent import TriageAgent

agent = TriageAgent()

cases = [
    {
        "label": "billing question -> resolver",
        "payload": {"message": "Why was I charged twice this month?", "history_summary": "No prior context."},
        "expect_intent": "billing",
        "expect_route": "resolver",
    },
    {
        "label": "explicit escalation request",
        "payload": {"message": "I want to speak to a real human agent right now.", "history_summary": "No prior context."},
        "expect_route": "escalation",
    },
    {
        "label": "technical question -> resolver",
        "payload": {"message": "The login page is broken, I keep getting an error.", "history_summary": "No prior context."},
        "expect_intent": "technical",
        "expect_route": "resolver",
    },
    {
        "label": "angry customer -> escalation",
        "payload": {"message": "This is absolutely unacceptable. I'm going to sue you.", "history_summary": "No prior context."},
        "expect_route": "escalation",
    },
]

all_passed = True
for i, case in enumerate(cases):
    if i > 0:
        time.sleep(13)  # stay under 5 RPM free-tier limit
    print(f"Running case {i+1}/{len(cases)}...")
    result = agent.run(case["payload"])
    checks = []

    if "expect_intent" in case:
        ok = result["intent"] == case["expect_intent"]
        checks.append(f"intent={result['intent']} ({'OK' if ok else 'FAIL expected ' + case['expect_intent']})")
        if not ok: all_passed = False

    if "expect_route" in case:
        ok = result["route"] == case["expect_route"]
        checks.append(f"route={result['route']} ({'OK' if ok else 'FAIL expected ' + case['expect_route']})")
        if not ok: all_passed = False

    checks.append(f"confidence={result['confidence']:.2f}")
    print(f"[{case['label']}] {' | '.join(checks)}")

print()
print("PASS" if all_passed else "FAIL — see above")
