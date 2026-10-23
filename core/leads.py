"""
core/leads.py

Tenant-scoped persistence for leasing-assistant leads. Every read and write is
keyed by client_id; nothing is reachable by lead id alone.
"""
import uuid
import csv
import io
from datetime import datetime, timezone, timedelta
from core.db import SessionLocal
from core.models import Lead
from core.leasing import summary_lines, QUESTION_CATALOG, CATALOG_ORDER
from core.utils import logger

OWNER_STATUSES = ("new", "contacted", "closed")
PARTIAL_AFTER_MINUTES = 20


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def lead_to_dict(l: Lead, config: dict | None = None) -> dict:
    return {
        "id": l.id,
        "session_id": l.session_id,
        "status": l.status,
        "owner_status": l.owner_status,
        "answers": dict(l.answers or {}),
        "summary": [{"label": a, "value": b} for a, b in summary_lines(l.answers or {}, config or {})],
        "flags": dict(l.flags or {}),
        "name": l.contact_name or "",
        "phone": l.contact_phone or "",
        "email": l.contact_email or "",
        "notify_kind": l.notify_kind,
        "notified_at": _iso(l.notified_at),
        "created_at": _iso(l.created_at),
        "updated_at": _iso(l.updated_at),
        "completed_at": _iso(l.completed_at),
    }


def get_lead_for_session(db, client_id: str, session_id: str) -> Lead | None:
    """Most recent lead for this chat session (any status)."""
    return (
        db.query(Lead)
        .filter(Lead.client_id == client_id, Lead.session_id == session_id)
        .order_by(Lead.created_at.desc())
        .first()
    )


def create_lead(db, client_id: str, session_id: str) -> Lead:
    now = _now()
    lead = Lead(
        id=str(uuid.uuid4()), client_id=client_id, session_id=session_id,
        status="in_progress", answers={}, flags={}, reask_count=0,
        owner_status="new", created_at=now, updated_at=now,
    )
    db.add(lead)
    db.flush()
    return lead


def sync_contact(lead: Lead) -> None:
    a = lead.answers or {}
    from core.leasing import _is_real
    lead.contact_name = a.get("name") if _is_real(a.get("name")) else lead.contact_name
    lead.contact_phone = a.get("phone") if _is_real(a.get("phone")) else lead.contact_phone
    lead.contact_email = a.get("email") if _is_real(a.get("email")) else lead.contact_email


def list_leads(client_id: str, config: dict | None = None, limit: int = 200) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Lead)
            .filter(Lead.client_id == client_id)
            .order_by(Lead.created_at.desc())
            .limit(limit)
            .all()
        )
        # Hide intake sessions that never captured anything at all.
        return [lead_to_dict(l, config) for l in rows if (l.answers or l.flags)]
    finally:
        db.close()


def set_owner_status(client_id: str, lead_id: str, status: str) -> dict | None:
    if status not in OWNER_STATUSES:
        raise ValueError("invalid status")
    db = SessionLocal()
    try:
        l = db.query(Lead).filter(Lead.client_id == client_id, Lead.id == lead_id).first()
        if not l:
            return None
        l.owner_status = status
        l.updated_at = _now()
        db.commit()
        db.refresh(l)
        return lead_to_dict(l)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def leads_csv(client_id: str) -> str:
    db = SessionLocal()
    try:
        rows = (
            db.query(Lead)
            .filter(Lead.client_id == client_id)
            .order_by(Lead.created_at.desc())
            .all()
        )
        buf = io.StringIO()
        w = csv.writer(buf)
        keys = CATALOG_ORDER
        w.writerow(["created_at", "status", "owner_status", "flags"] + [QUESTION_CATALOG[k]["label"] for k in keys])
        for l in rows:
            if not (l.answers or l.flags):
                continue
            a = l.answers or {}
            flags = ", ".join(k for k, v in (l.flags or {}).items() if v)
            row = [_iso(l.created_at), l.status, l.owner_status, flags] + [a.get(k, "") for k in keys]
            # Neutralize spreadsheet formula injection from prospect-typed text.
            w.writerow([("'" + str(v)) if str(v)[:1] in ("=", "+", "-", "@") else v for v in row])
        return buf.getvalue()
    finally:
        db.close()


def leasing_metrics(client_id: str, days: int = 30) -> dict:
    """The numbers an owner pays for, over the last `days` days: chats, leads
    (finished and partial), accommodation requests, and tours booked or
    canceled. Tenant-scoped like everything here."""
    from core.models import Conversation, Appointment
    now = _now()
    since = now - timedelta(days=days)

    def recent(dt):
        if dt is None:
            return False
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)) >= since

    db = SessionLocal()
    try:
        convs = db.query(Conversation.started_at).filter(Conversation.client_id == client_id).all()
        leads = db.query(Lead).filter(Lead.client_id == client_id).all()
        appts = db.query(Appointment).filter(Appointment.client_id == client_id).all()
    finally:
        db.close()

    conversations = sum(1 for (t,) in convs if recent(t))
    leads_recent = [l for l in leads if recent(l.created_at) and (l.answers or l.flags)]
    complete = sum(1 for l in leads_recent if l.status == "complete")
    partial = sum(1 for l in leads_recent if l.status != "complete" and (l.contact_phone or l.contact_email))
    after_hours = sum(1 for l in leads_recent if (l.flags or {}).get("after_hours"))
    accommodation = sum(1 for l in leads_recent if (l.flags or {}).get("accommodation_request"))
    appts_recent = [a for a in appts if recent(a.created_at)]
    upcoming = 0
    for a in appts:
        start = a.start_at if a.start_at.tzinfo else a.start_at.replace(tzinfo=timezone.utc)
        if a.status == "booked" and start > now:
            upcoming += 1
    return {
        "days": days,
        "conversations": conversations,
        "conversations_all_time": len(convs),
        "leads_complete": complete,
        "leads_partial": partial,
        "leads_after_hours": after_hours,
        "accommodation_requests": accommodation,
        "tours_booked": sum(1 for a in appts_recent),
        "tours_canceled": sum(1 for a in appts_recent if a.status == "canceled"),
        "tours_upcoming": upcoming,
        "lead_rate": round(100 * (complete + partial) / conversations) if conversations else 0,
    }


def stale_partial_leads(db, older_than_minutes: int = PARTIAL_AFTER_MINUTES):
    """In-progress leads with a way to reach the prospect that went quiet and
    were never announced to the owner, oldest first."""
    from sqlalchemy import or_
    cutoff = _now() - timedelta(minutes=older_than_minutes)
    return (
        db.query(Lead)
        .filter(
            Lead.status == "in_progress",
            Lead.notified_at.is_(None),
            Lead.updated_at < cutoff,
            or_(Lead.contact_phone.isnot(None), Lead.contact_email.isnot(None)),
        )
        .order_by(Lead.updated_at)
        .limit(100)
        .all()
    )


def claim_notification(db, lead_id: str, kind: str) -> bool:
    """Atomically mark a lead as announced. Only one caller (cron sweep, traffic
    sweep, or a chat turn) can win, so the owner never gets the same alert twice."""
    n = (
        db.query(Lead)
        .filter(Lead.id == lead_id, Lead.notified_at.is_(None))
        .update({"notified_at": _now(), "notify_kind": kind}, synchronize_session=False)
    )
    db.commit()
    return n == 1
