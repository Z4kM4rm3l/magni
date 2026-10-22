"""trial/subscription state, leasing config, leads, stripe event log

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clients", sa.Column("subscription_status", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("billing_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clients", sa.Column("vertical", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("lead_config", sa.JSON(), nullable=True))

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

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_index("ix_leads_client_session", table_name="leads")
    op.drop_index("ix_leads_client_created", table_name="leads")
    op.drop_index("ix_leads_session_id", table_name="leads")
    op.drop_index("ix_leads_client_id", table_name="leads")
    op.drop_index("ix_leads_id", table_name="leads")
    op.drop_table("leads")
    op.drop_column("clients", "lead_config")
    op.drop_column("clients", "vertical")
    op.drop_column("clients", "billing_synced_at")
    op.drop_column("clients", "subscription_status")
    op.drop_column("clients", "trial_ends_at")
