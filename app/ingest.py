"""Hourly processing run: pick up to N games not processed today and upsert them."""
import logging
import threading
import time
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .events import emit
from .models import Game, GamePlatformScore, ReviewSummary, ProcessingRun, ProcessedToday, DayProgress
from . import scraper, llm, youtube

log = logging.getLogger(__name__)
_run_lock = threading.Lock()


def today():
    return datetime.now(ZoneInfo(settings.tz)).date()


def select_candidates(s, fetcher, day, run_id):
    """Day logic: first New Releases, then browse 'new' page by page."""
    prog = s.get(DayProgress, day) or DayProgress(day=day, new_releases_done=False, next_browse_page=1)
    s.add(prog)
    s.commit()
    done = set(s.scalars(select(ProcessedToday.metacritic_url).where(ProcessedToday.day == day)))
    picked, sources = [], []
    n = settings.games_per_run
    if not prog.new_releases_done:
        links = scraper.parse_new_releases(fetcher.get(scraper.NEW_RELEASES_URL))
        picked += [u for u in links if u not in done][:n]
        prog.new_releases_done = True
        s.commit()
        emit(f"New Releases: найдено {len(links)} игр, новых для сегодня {len(picked)}", run_id, worker="scraper")
        sources.append("new_releases")
    pages = 0
    while len(picked) < n and pages < settings.max_browse_pages_per_run:
        page = prog.next_browse_page
        try:
            links = scraper.parse_browse(fetcher.get(scraper.browse_url(page)))
        except FileNotFoundError:
            emit(f"browse page {page}: нет fixture, стоп", run_id, worker="scraper", level="warn")
            break
        fresh = [u for u in links if u not in done and u not in picked]
        need = n - len(picked)
        picked += fresh[:need]
        sources.append(f"browse:p{page}")
        if len(fresh) <= need:  # page exhausted -> next run continues from the next page
            prog.next_browse_page = page + 1
        pages += 1
        s.commit()
        emit(f"Browse page {page}: найдено {len(links)} игр, взято {min(len(fresh), need)}", run_id, worker="scraper")
        if not links:
            break
    s.commit()
    return picked, ",".join(sources)


def upsert_game(s, g: scraper.GameData) -> Game:
    game = s.scalar(select(Game).where(Game.metacritic_url == g.url))
    if not game:
        game = Game(metacritic_url=g.url, slug=g.slug, title=g.title)
        s.add(game)
    for f in ["title", "cover_url", "developer", "publisher", "genres", "release_date", "description",
              "video_url", "metascore", "userscore"]:
        v = getattr(g, f)
        if v not in (None, [], ""):
            setattr(game, f, v)
    existing = {p.platform: p for p in game.platforms}
    for p in g.platforms:
        row = existing.get(p.name) or GamePlatformScore(platform=p.name)
        row.metascore, row.userscore, row.critic_reviews_count = p.metascore, p.userscore, p.critic_reviews_count
        if p.name not in existing:
            game.platforms.append(row)
    if game.userscore is None:
        us = [p.userscore for p in g.platforms if p.userscore is not None]
        game.userscore = max(us) if us else None
    s.flush()
    return game


def summarize(s, game: Game, g: scraper.GameData):
    for source, revs in (("critics", g.critic_reviews), ("users", g.user_reviews)):
        text, provider = llm.summarize_reviews(source, [asdict(r) for r in revs], game.id, game.title)
        row = game.summary(source)
        if not row:
            row = ReviewSummary(source=source)
            game.summaries.append(row)
        row.summary_text, row.reviews_used, row.provider = text, len(revs), provider


def run_once(trigger="scheduled", use_fixtures=None) -> int | None:
    if not _run_lock.acquire(blocking=False):
        emit("Запуск пропущен: предыдущий run ещё идёт", worker="scheduler", level="warn")
        return None
    fetcher = scraper.Fetcher(use_fixtures=use_fixtures)
    try:
        with SessionLocal() as s:
            run = ProcessingRun(trigger=trigger, status="running")
            s.add(run)
            s.commit()
            rid = run.id
            day = today()
            emit(f"Run #{rid} стартовал ({trigger}), день {day}", rid, status="running", processed=0, total=0)
            try:
                urls, src = select_candidates(s, fetcher, day, rid)
                run = s.get(ProcessingRun, rid)
                run.source = src
                s.commit()
                total = len(urls)
                emit(f"К обработке {total} игр ({run.source})", rid, status="running", processed=0, total=total)
                for i, url in enumerate(urls, 1):
                    try:
                        g = scraper.scrape_game(fetcher, url)
                        game = upsert_game(s, g)
                        summarize(s, game, g)
                        time.sleep(1.5)  # ease Gemini free-tier RPM
                        youtube.process(game)
                        s.add(ProcessedToday(metacritic_url=url, game_id=game.id, day=day, run_id=rid))
                        run.games_processed += 1
                        s.commit()
                        emit(f"[{i}/{total}] {game.title}: ок", rid, status="running", processed=i, total=total,
                             game_id=game.id)
                    except Exception as e:  # noqa
                        s.rollback()
                        run = s.get(ProcessingRun, rid)
                        run.games_failed += 1
                        s.commit()
                        emit(f"[{i}/{total}] {url}: ошибка {e}", rid, level="error", status="running",
                             processed=i, total=total)
                run.status = "success" if not run.games_failed else "partial"
            except Exception as e:  # noqa
                log.exception("run failed")
                s.rollback()
                run = s.get(ProcessingRun, rid)
                run.status, run.error = "failed", str(e)[:2000]
                emit(f"Run #{rid} упал: {e}", rid, level="error", status="failed")
            run.finished_at = datetime.utcnow()
            s.commit()
            emit(f"Run #{rid} завершён: {run.status}, обработано {run.games_processed}, ошибок {run.games_failed}",
                 rid, status="idle", processed=run.games_processed, total=run.games_processed + run.games_failed)
            return rid
    finally:
        fetcher.close()
        _run_lock.release()


def trigger_async(trigger="manual") -> bool:
    if _run_lock.locked():
        return False
    threading.Thread(target=run_once, kwargs={"trigger": trigger}, daemon=True).start()
    return True
