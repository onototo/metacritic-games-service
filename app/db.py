import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings


def _sqlalchemy_url(url: str) -> str:
    """Railway/Heroku give postgresql://; we ship psycopg2-binary, not psycopg v3."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg" not in url.split("://", 1)[0]:
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_db_url = _sqlalchemy_url(settings.database_url)
if _db_url.startswith("sqlite"):
    os.makedirs("data", exist_ok=True)
    engine = create_engine(_db_url, connect_args={"check_same_thread": False, "timeout": 30})
else:
    engine = create_engine(_db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db():
    from . import models  # noqa
    Base.metadata.create_all(engine)
