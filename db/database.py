"""Engine/session setup for the synthetic AML database."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base

DB_PATH = Path(__file__).parent / "aml.db"
ENGINE_URL = f"sqlite:///{DB_PATH}"


def get_engine(echo: bool = False):
    return create_engine(ENGINE_URL, echo=echo)


def init_db(engine) -> None:
    """Create all tables (no-op for tables that already exist)."""
    Base.metadata.create_all(engine)


def get_session(engine) -> Session:
    return Session(engine)
