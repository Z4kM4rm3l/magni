"""indexes for daily retention deletes and analytics counts

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_conv_started", "conversations", ["started_at"])
    op.create_index("ix_leads_created", "leads", ["created_at"])
    op.create_index("ix_appt_client_created", "appointments", ["client_id", "created_at"])
    op.create_index("ix_appt_end", "appointments", ["end_at"])


def downgrade() -> None:
    op.drop_index("ix_appt_end", table_name="appointments")
    op.drop_index("ix_appt_client_created", table_name="appointments")
    op.drop_index("ix_leads_created", table_name="leads")
    op.drop_index("ix_conv_started", table_name="conversations")
