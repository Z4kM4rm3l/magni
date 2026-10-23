#!/usr/bin/env python3
"""
scripts/apply_retention.py

Deletes chat transcripts older than 12 months, and leads and appointments
older than 24 months (core/retention.py). Schedule it daily, e.g. a Railway
cron service with `30 6 * * *` and DATABASE_URL.
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.retention import apply_retention

if __name__ == "__main__":
    counts = apply_retention()
    print(f"retention: deleted {counts['conversations']} chat(s), {counts['leads']} lead(s), "
          f"{counts['appointments']} appointment(s)")
