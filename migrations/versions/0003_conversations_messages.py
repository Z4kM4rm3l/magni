"""add conversations + messages (tenant-scoped)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=True),
        sa.Column("date", sa.String(), nullable=True),
        sa.Column("hour", sa.Integer(), nullable=True),
        sa.Column("intents", sa.JSON(), nullable=True),
        sa.Column("primary_intent", sa.String(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=True),
        sa.Column("resolution_source", sa.String(), nullable=True),
        sa.Column("resolution_confidence", sa.Float(), nullable=True),
        sa.Column("resolution_last_user", sa.Text(), nullable=True),
        sa.Column("resolution_last_agent", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_conversations_session_id", "conversations", ["session_id"])
    op.create_index("ix_conversations_client_id", "conversations", ["client_id"])
    op.create_index("ix_conv_client_started", "conversations", ["client_id", "started_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["conversations.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conv_client_started", table_name="conversations")
    op.drop_index("ix_conversations_client_id", table_name="conversations")
    op.drop_index("ix_conversations_session_id", table_name="conversations")
    op.drop_table("conversations")
