"""built-in scheduler: appointments

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_index(
        "uq_appt_client_slot_booked", "appointments", ["client_id", "start_at"], unique=True,
        postgresql_where=sa.text("status = 'booked'"), sqlite_where=sa.text("status = 'booked'"),
    )
    op.create_index(
        "uq_appt_lead_booked", "appointments", ["lead_id"], unique=True,
        postgresql_where=sa.text("status = 'booked' AND lead_id IS NOT NULL"),
        sqlite_where=sa.text("status = 'booked' AND lead_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_appt_lead_booked", table_name="appointments")
    op.drop_index("uq_appt_client_slot_booked", table_name="appointments")
    op.drop_index("ix_appt_client_start", table_name="appointments")
    op.drop_index("ix_appointments_cancel_token", table_name="appointments")
    op.drop_index("ix_appointments_lead_id", table_name="appointments")
    op.drop_index("ix_appointments_client_id", table_name="appointments")
    op.drop_index("ix_appointments_id", table_name="appointments")
    op.drop_table("appointments")
