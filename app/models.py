from datetime import datetime, date
from sqlalchemy import String, Integer, Float, Text, DateTime, Date, ForeignKey, UniqueConstraint, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return datetime.utcnow()


class Game(Base):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(primary_key=True)
    metacritic_url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(300), index=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    cover_url: Mapped[str | None] = mapped_column(String(1000))
    developer: Mapped[str | None] = mapped_column(String(500))
    publisher: Mapped[str | None] = mapped_column(String(500))
    genres: Mapped[list | None] = mapped_column(JSON, default=list)
    release_date: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str | None] = mapped_column(String(1000))
    metascore: Mapped[int | None] = mapped_column(Integer)
    userscore: Mapped[float | None] = mapped_column(Float)
    letsplay_url: Mapped[str | None] = mapped_column(String(1000))
    letsplay_title: Mapped[str | None] = mapped_column(String(500))
    letsplay_conclusion: Mapped[str | None] = mapped_column(Text)
    letsplay_status: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    platforms: Mapped[list["GamePlatformScore"]] = relationship(back_populates="game", cascade="all, delete-orphan", lazy="selectin")
    summaries: Mapped[list["ReviewSummary"]] = relationship(back_populates="game", cascade="all, delete-orphan", lazy="selectin")

    def summary(self, source):
        return next((s for s in self.summaries if s.source == source), None)


class GamePlatformScore(Base):
    __tablename__ = "game_platform_scores"
    __table_args__ = (UniqueConstraint("game_id", "platform"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(100), index=True)
    metascore: Mapped[int | None] = mapped_column(Integer)
    userscore: Mapped[float | None] = mapped_column(Float)
    critic_reviews_count: Mapped[int | None] = mapped_column(Integer)
    game: Mapped[Game] = relationship(back_populates="platforms")


class ReviewSummary(Base):
    __tablename__ = "review_summaries"
    __table_args__ = (UniqueConstraint("game_id", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(20))  # critics|users
    summary_text: Mapped[str | None] = mapped_column(Text)
    reviews_used: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    game: Mapped[Game] = relationship(back_populates="summaries")


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")
    source: Mapped[str | None] = mapped_column(String(100))
    games_processed: Mapped[int] = mapped_column(Integer, default=0)
    games_failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class ProcessedToday(Base):
    __tablename__ = "processed_today"
    __table_args__ = (UniqueConstraint("metacritic_url", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    metacritic_url: Mapped[str] = mapped_column(String(500), index=True)
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id", ondelete="SET NULL"))
    day: Mapped[date] = mapped_column(Date, index=True)
    run_id: Mapped[int | None] = mapped_column(Integer)


class DayProgress(Base):
    __tablename__ = "day_progress"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    new_releases_done: Mapped[bool] = mapped_column(Boolean, default=False)
    next_browse_page: Mapped[int] = mapped_column(Integer, default=1)


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    worker: Mapped[str] = mapped_column(String(50), default="ingest")
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSON)
