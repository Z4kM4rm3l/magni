"""rental-only: retire the "general" business type, rental chat defaults

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-23
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Magni now serves rental businesses only. Accounts on the old "general"
    # type (or none) become long-term rentals; their saved lead settings are
    # re-validated for that type the next time they're read.
    op.execute("UPDATE clients SET vertical = 'long_term' WHERE vertical IS NULL OR vertical NOT IN ('long_term', 'seasonal')")
    # Rental-focused defaults for owners who never changed the old generic ones.
    op.execute("UPDATE clients SET bot_name = 'Leasing Assistant' WHERE bot_name IS NULL OR bot_name = 'Magni'")
    op.execute("UPDATE clients SET welcome_message = 'Hi! Looking for a place? I can check availability and book a tour.' "
               "WHERE welcome_message IS NULL OR welcome_message = 'Hi! How can I help you today?'")


def downgrade() -> None:
    pass  # nothing to undo: "general" no longer exists in the code
