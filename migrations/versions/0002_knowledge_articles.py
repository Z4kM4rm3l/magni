"""add knowledge_articles (tenant-scoped KB)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_articles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_articles_id", "knowledge_articles", ["id"])
    op.create_index("ix_knowledge_articles_client_id", "knowledge_articles", ["client_id"])
    op.create_index("ix_kb_client_updated", "knowledge_articles", ["client_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_kb_client_updated", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_client_id", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_id", table_name="knowledge_articles")
    op.drop_table("knowledge_articles")
