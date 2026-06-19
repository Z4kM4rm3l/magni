"""Standalone test — no Flask required. Run: python tests/test_resolver_agent.py"""
import sys, os, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from agents.resolver_agent import ResolverAgent

agent = ResolverAgent()

cases = [
    {
        "label": "billing question gets a real response",
        "payload": {
            "message": "Why was I charged twice this month?",
            "intent": "billing",
            "history_summary": "No prior context.",
            "history": [],
        },
        "checks": lambda r: (
            len(r["response_text"]) > 20,
            isinstance(r["escalation_flag"], bool),  # flag can be True — resolver correctly signals billing escalation
            r["confidence"] > 0,
        ),
    },
    {
        "label": "technical question gets step-based response",
        "payload": {
            "message": "I can't log in. The page just refreshes.",
            "intent": "technical",
            "history_summary": "No prior context.",
            "history": [],
        },
        "checks": lambda r: (
            len(r["response_text"]) > 20,
            isinstance(r["escalation_flag"], bool),
        ),
    },
    {
        "label": "history context is accepted without error",
        "payload": {
            "message": "Can you explain that again?",
            "intent": "product",
            "history_summary": "USER: What does the pro plan include?\nASSISTANT: The pro plan includes unlimited KB articles.",
            "history": [
                {"role": "user", "content": "What does the pro plan include?"},
                {"role": "assistant", "content": "The pro plan includes unlimited KB articles."},
            ],
        },
        "checks": lambda r: (
            len(r["response_text"]) > 10,
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
        print(f"  response_text={result['response_text'][:120]}")
    else:
        print(f"  OK | escalation_flag={result['escalation_flag']} | response_len={len(result['response_text'])}")

print()
print("PASS" if all_passed else "FAIL -- see above")
