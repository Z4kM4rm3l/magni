"""add password_hash to clients (client portal self-serve login)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("password_hash", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "password_hash")
