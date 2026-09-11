import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Resolve the URL first. 
# It checks Railway's env var, then falls back to local if not found.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/magni")

# 2. Initialize the engine ONLY ONCE with the resolved URL
# Railway's internal Postgres hostname (postgres.railway.internal) does not
# support SSL — SSL is terminated at the external proxy only. Omit connect_args
# so psycopg2 uses its default negotiation, which works on both paths.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# 3. Thread-safe session manufacturing
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. Base class for declarative data model definitions
Base = declarative_base()

def init_db():
    """Ensure the database schema exists and is current.

    Alembic is the single source of truth for schema. create_all is no longer
    used, because it can add missing tables but cannot evolve existing ones.
    This applies any pending migrations up to head, so app startup and the
    setup scripts still self-heal a fresh database.

    On a database that predates Alembic (its tables already exist but there is
    no alembic_version table), run `alembic stamp 0001` once before the first
    upgrade so the baseline migration is not re-applied.
    """
    import core.models  # register models on Base.metadata
    from alembic.config import Config
    from alembic import command
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    command.upgrade(Config(os.path.join(repo_root, "alembic.ini")), "head")