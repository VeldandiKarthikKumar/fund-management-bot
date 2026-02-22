"""Database connection and session management."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings
from src.db.models import Base


def get_engine():
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,   # Detect stale connections
        pool_size=5,
        max_overflow=10,
    )


def create_tables():
    """Create all tables. Called on startup or via migration."""
    engine = get_engine()
    Base.metadata.create_all(engine)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


@contextmanager
def get_session() -> Session:
    """Context manager for DB sessions with automatic rollback on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
