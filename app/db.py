import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings

if settings.database_url.startswith("sqlite"):
    os.makedirs("data", exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30})
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db():
    from . import models  # noqa
    Base.metadata.create_all(engine)
