"""Standalone test — no Flask required. Run: python tests/test_escalation_agent.py"""
import sys, os, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from agents.escalation_agent import EscalationAgent

agent = EscalationAgent()

VALID_ACTIONS = {"collect_contact", "human_handoff", "deflect"}

cases = [
    {
        "label": "legal threat -> human_handoff + alert_client",
        "payload": {
            "message": "I'm going to sue your company. This is fraud.",
            "intent": "billing",
            "history_summary": "USER: I was charged $300 I didn't authorize.",
            "escalation_reason": "Legal threat detected.",
        },
        "checks": lambda r: (
            r["action"] in VALID_ACTIONS,
            r["alert_client"] is True,
            len(r["message_to_user"]) > 20,
        ),
    },
    {
        "label": "human request -> collect_contact or human_handoff",
        "payload": {
            "message": "I just want to talk to a real person.",
            "intent": "general",
            "history_summary": "No prior context.",
            "escalation_reason": "Customer explicitly requested human agent.",
        },
        "checks": lambda r: (
            r["action"] in ("collect_contact", "human_handoff"),
            len(r["message_to_user"]) > 20,
        ),
    },
    {
        "label": "ambiguous frustration -> valid action",
        "payload": {
            "message": "This is so frustrating, nothing is working.",
            "intent": "technical",
            "history_summary": "USER: My login is broken.\nASSISTANT: Try clearing your cache.",
            "escalation_reason": "High frustration detected.",
        },
        "checks": lambda r: (
            r["action"] in VALID_ACTIONS,
            len(r["message_to_user"]) > 20,
        ),
    },
]

all_passed = True
for i, case in enumerate(cases):
    if i > 0:
        time.sleep(13)
    print(f"Running case {i+1}/{len(cases)}: {case['label']}")
    result = agent.run(case["payload"])
    check_results = case["checks"](result)
    if not isinstance(check_results, tuple):
        check_results = (check_results,)
    ok = all(check_results)
    if not ok:
        all_passed = False
        print(f"  FAIL: checks={check_results}")
        print(f"  result={result}")
    else:
        print(f"  OK | action={result['action']} | alert_client={result['alert_client']}")

print()
print("PASS" if all_passed else "FAIL -- see above")
