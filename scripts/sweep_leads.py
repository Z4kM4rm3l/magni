#!/usr/bin/env python3
"""
scripts/sweep_leads.py

Alerts owners about prospects who shared a phone or email, then stopped
replying before finishing the leasing questions. Live chat traffic already runs
this about once a minute; schedule this script (e.g. a Railway cron every 10
minutes) so alerts still go out during quiet hours.

Run: python scripts/sweep_leads.py
"""
import os
import sys

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import notifier
from core.lead_flow import sweep_partial_leads

if __name__ == "__main__":
    notifier.RUN_IN_BACKGROUND = False  # finish sending before the process exits
    sent = sweep_partial_leads()
    print(f"sweep_leads: {sent} partial lead alert(s) sent")
