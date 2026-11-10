"""remove the rental features: leads, appointments, business type, leasing config

Magni is a general customer service assistant. The leasing intake, scheduler
and business types are gone from the code, so their storage goes too. The one
setting worth keeping, where the owner's alerts go, moves out of the leasing
config into two plain columns.

This deletes the leads and appointments tables and their rows. Export anything
you still need before upgrading a database that has real ones.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-09
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_RENTAL_WELCOME = "Hi! Looking for a place? I can check availability and book a tour."


def upgrade() -> None:
    op.add_column("clients", sa.Column("notify_email", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("notify_phone", sa.String(), nullable=True))

    # Carry each account's alert email / phone over from the leasing config.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, lead_config FROM clients WHERE lead_config IS NOT NULL")).fetchall()
    for client_id, raw in rows:
        try:
            cfg = json.loads(raw) if isinstance(raw, (str, bytes)) else (raw or {})
        except (TypeError, ValueError):
            cfg = {}
        if not isinstance(cfg, dict):
            continue
        email = str(cfg.get("notify_email") or "").strip() or None
        phone = str(cfg.get("notify_phone") or "").strip() or None
        if email or phone:
            bind.execute(sa.text("UPDATE clients SET notify_email = :e, notify_phone = :p WHERE id = :id"),
                         {"e": email, "p": phone, "id": client_id})

    # Accounts still on the leasing defaults get the general ones. Anything an
    # owner customized is left alone.
    bind.execute(sa.text("UPDATE clients SET bot_name = 'Assistant' WHERE bot_name = 'Leasing Assistant'"))
    bind.execute(sa.text("UPDATE clients SET welcome_message = 'Hi! How can I help you today?' "
                         "WHERE welcome_message = :w"), {"w": _RENTAL_WELCOME})

    op.drop_table("appointments")
    op.drop_table("leads")
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("lead_config")
        batch.drop_column("vertical")


def downgrade() -> None:
    # Brings the structure back (empty) so earlier migrations can still be
    # downgraded. The deleted leads and appointments are not restored.
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("vertical", sa.String(), nullable=True))
        batch.add_column(sa.Column("lead_config", sa.JSON(), nullable=True))
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE clients SET vertical = 'general'"))
    rows = bind.execute(sa.text(
        "SELECT id, notify_email, notify_phone FROM clients "
        "WHERE notify_email IS NOT NULL OR notify_phone IS NOT NULL")).fetchall()
    for client_id, email, phone in rows:
        bind.execute(sa.text("UPDATE clients SET lead_config = :c WHERE id = :id"),
                     {"c": json.dumps({"notify_email": email or "", "notify_phone": phone or ""}), "id": client_id})

    op.create_table(
        "leads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("pending_key", sa.String(), nullable=True),
        sa.Column("reask_count", sa.Integer(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("contact_phone", sa.String(), nullable=True),
        sa.Column("contact_email", sa.String(), nullable=True),
        sa.Column("owner_status", sa.String(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_kind", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_leads_id", "leads", ["id"])
    op.create_index("ix_leads_client_id", "leads", ["client_id"])
    op.create_index("ix_leads_session_id", "leads", ["session_id"])
    op.create_index("ix_leads_client_created", "leads", ["client_id", "created_at"])
    op.create_index("ix_leads_client_session", "leads", ["client_id", "session_id"])
    op.create_index("ix_leads_created", "leads", ["created_at"])

    op.create_table(
        "appointments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("lead_id", sa.String(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("property", sa.String(), nullable=True),
        sa.Column("cancel_token", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_by", sa.String(), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointments_id", "appointments", ["id"])
    op.create_index("ix_appointments_client_id", "appointments", ["client_id"])
    op.create_index("ix_appointments_lead_id", "appointments", ["lead_id"])
    op.create_index("ix_appointments_cancel_token", "appointments", ["cancel_token"], unique=True)
    op.create_index("ix_appt_client_start", "appointments", ["client_id", "start_at"])
    op.create_index("ix_appt_client_created", "appointments", ["client_id", "created_at"])
    op.create_index("ix_appt_end", "appointments", ["end_at"])
    op.create_index(
        "uq_appt_client_slot_booked", "appointments", ["client_id", "start_at"], unique=True,
        postgresql_where=sa.text("status = 'booked'"), sqlite_where=sa.text("status = 'booked'"),
    )
    op.create_index(
        "uq_appt_lead_booked", "appointments", ["lead_id"], unique=True,
        postgresql_where=sa.text("status = 'booked' AND lead_id IS NOT NULL"),
        sqlite_where=sa.text("status = 'booked' AND lead_id IS NOT NULL"),
    )

    with op.batch_alter_table("clients") as batch:
        batch.drop_column("notify_phone")
        batch.drop_column("notify_email")
