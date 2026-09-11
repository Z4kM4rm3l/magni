"""baseline: existing clients table (pre-Alembic schema)

Represents the schema as create_all() built it before Alembic was introduced.
On a database that already has the clients table, run `alembic stamp 0001`
instead of upgrading, so this is not re-applied.

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("api_key", sa.String(), nullable=False),
        sa.Column("business_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tier", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("monthly_limit", sa.Integer(), nullable=True),
        sa.Column("monthly_used", sa.Integer(), nullable=True),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bot_name", sa.String(), nullable=True),
        sa.Column("primary_color", sa.String(), nullable=True),
        sa.Column("welcome_message", sa.String(), nullable=True),
        sa.Column("allowed_domains", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clients_id", "clients", ["id"])
    op.create_index("ix_clients_api_key", "clients", ["api_key"], unique=True)
    op.create_index("ix_clients_stripe_customer_id", "clients", ["stripe_customer_id"], unique=True)
    op.create_index("ix_clients_stripe_subscription_id", "clients", ["stripe_subscription_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_clients_stripe_subscription_id", table_name="clients")
    op.drop_index("ix_clients_stripe_customer_id", table_name="clients")
    op.drop_index("ix_clients_api_key", table_name="clients")
    op.drop_index("ix_clients_id", table_name="clients")
    op.drop_table("clients")
