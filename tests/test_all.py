import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test.db")
os.environ["SCRAPER_USE_FIXTURES"] = "1"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_LOGS_DIR"] = "data/test_llm_logs"
os.environ["RUN_SCHEDULER"] = "0"

import glob
from app import scraper
from app.config import settings

FX = "tests/fixtures"


def read(url):
    return open(os.path.join(FX, scraper.fixture_name(url)), encoding="utf-8").read()


def test_parse_new_releases():
    links = scraper.parse_new_releases(read(scraper.NEW_RELEASES_URL))
    assert 10 <= len(links) <= 40 and all(l.startswith("https://www.metacritic.com/game/") for l in links)


def test_parse_browse():
    assert len(scraper.parse_browse(read(scraper.browse_url(1)))) >= 20


def test_parse_game():
    url = scraper.parse_new_releases(read(scraper.NEW_RELEASES_URL))[0]
    g = scraper.parse_game(read(url), url)
    assert g.title and g.cover_url and g.platforms and g.description


def test_mock_summary_and_jsonl():
    from app import llm
    text, prov = llm.summarize_reviews("users", [{"text": "Great gameplay and story", "score": 9},
                                                 {"text": "Buggy, bad performance", "score": 3}], 1, "X")
    assert prov == "mock" and "Нравится" in text
    f = sorted(glob.glob("data/test_llm_logs/*.jsonl"))[-1]
    rec = json.loads(open(f, encoding="utf-8").read().strip().splitlines()[-1])
    assert rec["purpose"] == "review_summary_users" and rec["output"] == text


def test_seed_run_and_api():
    if os.path.exists("data/test.db"):
        os.remove("data/test.db")
    from app.db import init_db
    from app import ingest
    init_db()
    rid = ingest.run_once(trigger="test", use_fixtures=True)
    assert rid
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"
    games = c.get("/api/games?sort=metascore").json()
    assert len(games) >= 5
    ms = [g["metascore"] for g in games if g["metascore"] is not None]
    assert ms == sorted(ms, reverse=True)
    d = c.get(f"/api/games/{games[0]['id']}").json()
    assert "critics" in d["summaries"] and "similar" in d
    plat = games[0]["platforms"][0]["platform"]
    assert all(any(p["platform"] == plat for p in g["platforms"]) for g in c.get("/api/games", params={"platform": plat}).json())
    assert c.get("/").status_code == 200 and c.get(f"/games/{games[0]['id']}").status_code == 200
    assert c.get("/monitor").status_code == 200
    # second run same day must not re-take new releases
    from app.db import SessionLocal
    from app.models import DayProgress
    with SessionLocal() as s:
        assert s.get(DayProgress, ingest.today()).new_releases_done
