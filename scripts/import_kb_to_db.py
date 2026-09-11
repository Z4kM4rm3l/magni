"""
scripts/import_kb_to_db.py

One-time importer: moves legacy file-backed articles from data/knowledge_base.json
into the tenant-scoped knowledge_articles table, assigning them to an explicit
owner client.

The owner is required and never guessed, so knowledge cannot be silently
attached to the wrong tenant. Identify the owner by --client-id or --api-key.

Idempotent: re-running skips articles whose id is already present. Legacy article
ids are preserved on import, which is what makes the re-run check reliable.

Prerequisite: the knowledge_articles table must already exist (created by the
Alembic migration). This script imports data only; it does not create schema.

Usage:
    python scripts/import_kb_to_db.py --client-id <uuid> [--dry-run]
    python scripts/import_kb_to_db.py --api-key sk_magni_xxxxxxxx [--dry-run]
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone

# Allow running as a plain script from the repo root (python scripts/...).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import inspect

from core.db import SessionLocal, engine
from core.models import Client, KnowledgeArticle

KB_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "knowledge_base.json")
)


def _load_legacy() -> list:
    if not os.path.exists(KB_FILE):
        return []
    with open(KB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_dt(value):
    """Legacy epoch float -> timezone-aware datetime. None passes through."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _resolve_client(db, client_id: str | None, api_key: str | None) -> Client | None:
    q = db.query(Client)
    if client_id:
        return q.filter(Client.id == client_id).first()
    return q.filter(Client.api_key == api_key).first()


def main():
    parser = argparse.ArgumentParser(
        description="Import legacy JSON KB into the tenant-scoped knowledge_articles table."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--client-id", help="Owner client id (uuid).")
    group.add_argument("--api-key", help="Owner client API key.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen and write nothing.",
    )
    args = parser.parse_args()

    # Schema must exist first (migrations own table creation, not this script).
    if not inspect(engine).has_table("knowledge_articles"):
        print("ERROR: table 'knowledge_articles' does not exist. "
              "Run the Alembic migration before importing.")
        sys.exit(1)

    legacy = _load_legacy()
    if not legacy:
        print(f"No legacy articles found at {KB_FILE}. Nothing to import.")
        return

    db = SessionLocal()
    try:
        owner = _resolve_client(db, args.client_id, args.api_key)
        if not owner:
            ident = args.client_id or args.api_key
            print(f"ERROR: no client found for '{ident}'. "
                  f"Aborting so nothing is misassigned.")
            sys.exit(1)

        print(f"Owner: {owner.business_name or '(unnamed)'} "
              f"(id={owner.id[:8]}, tier={owner.tier})")
        print(f"Legacy articles found: {len(legacy)}"
              f"{'  [DRY RUN]' if args.dry_run else ''}\n")

        inserted = skipped_existing = skipped_conflict = 0
        for art in legacy:
            aid = str(art.get("id"))
            existing = (
                db.query(KnowledgeArticle)
                .filter(KnowledgeArticle.id == aid)
                .first()
            )
            if existing:
                if existing.client_id == owner.id:
                    skipped_existing += 1
                    print(f"  = id={aid} already imported for this client; skipping.")
                else:
                    skipped_conflict += 1
                    print(f"  ! id={aid} already owned by another client "
                          f"({existing.client_id[:8]}); skipping.")
                continue

            record = KnowledgeArticle(
                id=aid,
                client_id=owner.id,
                title=art.get("title", "Untitled"),
                content=art.get("content", ""),
                summary=art.get("summary"),
                category=art.get("category", "general") or "general",
                created_at=_to_dt(art.get("created_at")) or datetime.now(timezone.utc),
                updated_at=_to_dt(art.get("updated_at")) or datetime.now(timezone.utc),
            )
            if not args.dry_run:
                db.add(record)
            inserted += 1
            print(f"  + id={aid} \"{record.title}\" -> client {owner.id[:8]}")

        if args.dry_run:
            db.rollback()
            print(f"\n[dry-run] would insert {inserted}, "
                  f"skip {skipped_existing} already-present, "
                  f"{skipped_conflict} conflicts. No changes written.")
        else:
            db.commit()
            print(f"\nDone. Inserted {inserted}, "
                  f"skipped {skipped_existing} already-present, "
                  f"{skipped_conflict} conflicts.")
    except Exception as e:
        db.rollback()
        print(f"ERROR during import: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
