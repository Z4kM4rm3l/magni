#!/usr/bin/env python3
"""
scripts/retry_alerts.py

Retries any "needs a person" alert email that failed to send (email provider
down, a bad setting). Live chat traffic already does this about once a minute;
schedule this script (e.g. a Railway cron every 10 minutes) so alerts still go
out during quiet hours.

Run: python scripts/retry_alerts.py
"""
import os
import sys

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import notifier
from core.escalations import retry_unsent

if __name__ == "__main__":
    notifier.RUN_IN_BACKGROUND = False  # finish sending before the process exits
    print(f"retry_alerts: {retry_unsent()} alert(s) delivered")
