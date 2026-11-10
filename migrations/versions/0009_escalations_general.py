"""escalations table (chats that need a person), for every business type

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escalations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("last_message", sa.Text(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(), nullable=True),
        sa.Column("ask_count", sa.Integer(), nullable=True),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("contact_email", sa.String(), nullable=True),
        sa.Column("contact_phone", sa.String(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contact_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_attempts", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_escalations_id", "escalations", ["id"])
    op.create_index("ix_escalations_client_id", "escalations", ["client_id"])
    op.create_index("ix_escalations_session_id", "escalations", ["session_id"])
    op.create_index("ix_esc_client_created", "escalations", ["client_id", "created_at"])
    op.create_index("ix_esc_client_session", "escalations", ["client_id", "session_id"])
    op.create_index("ix_esc_created", "escalations", ["created_at"])
    # Existing accounts keep their business type (0007 made them all rentals).
    # New accounts start as "general"; that default lives in the app
    # (core/leasing.DEFAULT_VERTICAL), so there is no data change here.


def downgrade() -> None:
    op.drop_index("ix_esc_created", table_name="escalations")
    op.drop_index("ix_esc_client_session", table_name="escalations")
    op.drop_index("ix_esc_client_created", table_name="escalations")
    op.drop_index("ix_escalations_session_id", table_name="escalations")
    op.drop_index("ix_escalations_client_id", table_name="escalations")
    op.drop_index("ix_escalations_id", table_name="escalations")
    op.drop_table("escalations")
