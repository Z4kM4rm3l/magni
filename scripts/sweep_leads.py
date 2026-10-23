#!/usr/bin/env python3
"""
scripts/sweep_leads.py

Alerts owners about prospects who shared a phone or email, then stopped
replying before finishing the leasing questions, and sends day-before
appointment reminders. Live chat traffic already runs
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
from core.lead_flow import sweep_partial_leads, send_due_reminders

if __name__ == "__main__":
    if not os.getenv("PUBLIC_BASE_URL", "").strip():
        sys.exit("PUBLIC_BASE_URL is not set; reminder emails need it for their cancel links.")
    notifier.RUN_IN_BACKGROUND = False  # finish sending before the process exits
    sent = sweep_partial_leads()
    reminders = send_due_reminders()
    print(f"sweep_leads: {sent} partial lead alert(s), {reminders} appointment reminder(s) sent")
