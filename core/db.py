import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Resolve the URL first. 
# It checks Railway's env var, then falls back to local if not found.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/magni")

# 2. Initialize the engine ONLY ONCE with the resolved URL
engine = create_engine(
    DATABASE_URL, 
    pool_pre_ping=True,  # Disconnect recovery safeguard
    pool_size=10,        # Number of persistent connections
    max_overflow=20      # Temporary burst connection allowance
)

# 3. Thread-safe session manufacturing
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. Base class for declarative data model definitions
Base = declarative_base()

def init_db():
    """Generates all relational database tables cleanly if they do not yet exist."""
    import core.models  # Imports models to register them with Base.metadata
    Base.metadata.create_all(bind=engine)