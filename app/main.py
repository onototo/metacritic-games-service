import asyncio
import json
import logging
from datetime import datetime

from fastapi import FastAPI, Request, Depends, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_

from .config import settings
from .db import SessionLocal, init_db
from .models import Game, GamePlatformScore, ProcessingRun, JobEvent, ProcessedToday
from .similar import similar_games
from . import ingest, llm, youtube
from .events import WORKER_STATE

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Metacritic Games Service")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
scheduler = None


@app.on_event("startup")
def startup():
    global scheduler
    init_db()
    if settings.run_scheduler:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone=settings.tz)
        scheduler.add_job(ingest.run_once, "interval", minutes=settings.schedule_minutes, id="hourly",
                          kwargs={"trigger": "scheduled"}, max_instances=1, coalesce=True,
                          next_run_time=datetime.now())
        scheduler.start()
        WORKER_STATE["scheduler"] = {"status": "running", "message": f"каждые {settings.schedule_minutes} мин"}


def db():
    with SessionLocal() as s:
        yield s


def check_admin(x_admin_token: str | None = Header(None), token: str | None = Query(None)):
    if settings.admin_token and settings.admin_token not in (x_admin_token, token):
        raise HTTPException(401, "admin token required")


def game_dict(g: Game, full=False):
    d = {"id": g.id, "title": g.title, "slug": g.slug, "cover_url": g.cover_url, "metascore": g.metascore,
         "userscore": g.userscore, "developer": g.developer, "release_date": g.release_date,
         "platforms": [{"platform": p.platform, "metascore": p.metascore, "userscore": p.userscore} for p in g.platforms],
         "metacritic_url": g.metacritic_url}
    if full:
        d.update(description=g.description, video_url=g.video_url, publisher=g.publisher, genres=g.genres,
                 summaries={s.source: s.summary_text for s in g.summaries},
                 letsplay={"url": g.letsplay_url, "title": g.letsplay_title, "conclusion": g.letsplay_conclusion,
                           "status": g.letsplay_status}, updated_at=g.updated_at)
    return d


SORTS = {"metascore": Game.metascore.desc().nullslast(), "userscore": Game.userscore.desc().nullslast(),
         "title": Game.title.asc(), "updated": Game.updated_at.desc(),
         "metascore_asc": Game.metascore.asc().nullslast()}


def _platforms_arg(platform) -> list[str] | None:
    if platform is None:
        return None
    if isinstance(platform, str):
        platform = [platform]
    plats = []
    for p in platform:
        p = (p or "").strip()
        if p and p not in plats:
            plats.append(p)
    return plats or None


def query_games(s, q=None, platform=None, sort="metascore"):
    st = select(Game)
    if q:
        st = st.where(Game.title.ilike(f"%{q}%"))
    plats = _platforms_arg(platform)
    if plats:
        st = st.where(Game.platforms.any(GamePlatformScore.platform.in_(plats)))
    return list(s.scalars(st.order_by(SORTS.get(sort, SORTS["metascore"]), Game.id)))


def all_platforms(s):
    return list(s.scalars(select(GamePlatformScore.platform).distinct().order_by(GamePlatformScore.platform)))


# ---------------- API ----------------
@app.get("/health")
def health(s=Depends(db)):
    s.execute(select(1))
    return {"status": "ok", "llm_provider": llm.detect_provider(), "fixtures_mode": settings.scraper_use_fixtures,
            "youtube_enabled": youtube.enabled()}


@app.get("/api/games")
def api_games(q: str | None = None, platform: list[str] | None = Query(None), sort: str = "metascore", s=Depends(db)):
    return [game_dict(g) for g in query_games(s, q, platform, sort)]


@app.get("/api/games/{gid}")
def api_game(gid: int, s=Depends(db)):
    g = s.get(Game, gid) or HTTPException(404)
    if isinstance(g, HTTPException):
        raise g
    d = game_dict(g, full=True)
    d["similar"] = [{"id": x.id, "title": x.title, "score": round(sc, 3)} for sc, x in similar_games(g, query_games(s))]
    return d


@app.get("/api/platforms")
def api_platforms(s=Depends(db)):
    return all_platforms(s)


@app.get("/api/runs")
def api_runs(s=Depends(db)):
    return [{"id": r.id, "status": r.status, "trigger": r.trigger, "source": r.source, "started_at": r.started_at,
             "finished_at": r.finished_at, "games_processed": r.games_processed, "games_failed": r.games_failed,
             "error": r.error} for r in s.scalars(select(ProcessingRun).order_by(ProcessingRun.id.desc()).limit(20))]


@app.post("/api/runs/trigger", dependencies=[Depends(check_admin)])
def api_trigger():
    ok = ingest.trigger_async("manual")
    return {"started": ok, "message": "run started" if ok else "run already in progress"}


def status_snapshot(s):
    last = s.scalar(select(ProcessingRun).order_by(ProcessingRun.id.desc()).limit(1))
    day = ingest.today()
    return {"workers": WORKER_STATE, "running": ingest._run_lock.locked(),
            "games_total": s.scalar(select(func.count(Game.id))),
            "processed_today": s.scalar(select(func.count(ProcessedToday.id)).where(ProcessedToday.day == day)),
            "runs_total": s.scalar(select(func.count(ProcessingRun.id))),
            "next_run": str(scheduler.get_job("hourly").next_run_time) if scheduler and scheduler.get_job("hourly") else None,
            "last_run": {"id": last.id, "status": last.status, "processed": last.games_processed,
                         "failed": last.games_failed, "trigger": last.trigger, "source": last.source,
                         "started_at": str(last.started_at)} if last else None}


@app.get("/api/status")
def api_status(s=Depends(db)):
    return status_snapshot(s)


@app.get("/api/events/stream")
async def events_stream(request: Request):
    async def gen():
        with SessionLocal() as s:
            last_id = (s.scalar(select(func.max(JobEvent.id))) or 0) - 30
        while not await request.is_disconnected():
            with SessionLocal() as s:
                evs = list(s.scalars(select(JobEvent).where(JobEvent.id > last_id).order_by(JobEvent.id).limit(100)))
                snap = status_snapshot(s)
            for e in evs:
                last_id = e.id
                yield f"event: job\ndata: {json.dumps({'id': e.id, 'ts': str(e.ts), 'worker': e.worker, 'level': e.level, 'message': e.message, 'run_id': e.run_id}, ensure_ascii=False)}\n\n"
            yield f"event: status\ndata: {json.dumps(snap, ensure_ascii=False, default=str)}\n\n"
            await asyncio.sleep(1.5)
    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- UI ----------------
@app.get("/", response_class=HTMLResponse)
def ui_list(request: Request, q: str | None = None, platform: list[str] | None = Query(None), sort: str = "metascore", s=Depends(db)):
    plats = _platforms_arg(platform) or []
    games = query_games(s, q, plats, sort)
    ctx = {"request": request, "games": games, "q": q or "", "platform": plats, "sort": sort,
           "platforms": all_platforms(s)}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "_games.html", ctx)
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/games/{gid}", response_class=HTMLResponse)
def ui_game(request: Request, gid: int, s=Depends(db)):
    g = s.get(Game, gid)
    if not g:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "game.html", {"request": request, "g": g,
                                                    "similar": similar_games(g, query_games(s))})


@app.get("/monitor", response_class=HTMLResponse)
def ui_monitor(request: Request):
    return templates.TemplateResponse(request, "monitor.html", {"request": request, "need_token": bool(settings.admin_token)})
