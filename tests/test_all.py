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


def test_cover_url_from_ld_or_hero():
    coverage = scraper.parse_game(read("https://www.metacritic.com/game/coverage/"),
                                  "https://www.metacritic.com/game/coverage/")
    assert coverage.title == "Coverage"
    assert coverage.cover_url is None
    assert coverage.metascore is None and coverage.userscore is None

    nie = scraper.parse_game(read("https://www.metacritic.com/game/nie-xiaoqian/"),
                             "https://www.metacritic.com/game/nie-xiaoqian/")
    assert nie.cover_url is None
    assert nie.metascore is None and nie.userscore is None

    related = '''<script type="application/ld+json">{"@type":"VideoGame","name":"Zukontide"}</script>
        <div data-testid="product-card"><img alt="Plants vs. Zombies" src="https://www.metacritic.com/a/img/catalog/provider/pvz.jpg"></div>'''
    assert scraper.parse_game(related, "https://www.metacritic.com/game/zukontide/").cover_url is None

    as_dict = '''<script type="application/ld+json">{"@type":"VideoGame","name":"Dict Cover",
        "image":{"url":"https://www.metacritic.com/a/img/catalog/provider/from-dict.jpg"}}</script>'''
    assert scraper.parse_game(as_dict, "https://www.metacritic.com/game/dict-cover/").cover_url.endswith("from-dict.jpg")

    as_list = '''<script type="application/ld+json">{"@type":"VideoGame","name":"List Cover",
        "image":[{"contentUrl":"https://www.metacritic.com/a/img/catalog/provider/from-list.jpg"}]}</script>
        <img src="https://www.metacritic.com/a/img/catalog/provider/dom-should-lose.jpg">'''
    assert scraper.parse_game(as_list, "https://www.metacritic.com/game/list-cover/").cover_url.endswith("from-list.jpg")

    srcset = '''<script type="application/ld+json">{"@type":"VideoGame","name":"Srcset Cover"}</script>
        <div data-testid="hero-images"><img srcset="https://www.metacritic.com/a/img/catalog/provider/hero.jpg 1x, https://x/2.jpg 2x"></div>
        <div data-testid="product-card"><img src="https://www.metacritic.com/a/img/catalog/provider/later.jpg"></div>'''
    assert scraper.parse_game(srcset, "https://www.metacritic.com/game/srcset-cover/").cover_url.endswith("hero.jpg")

    pb_url = "https://www.metacritic.com/game/phantom-blade-zero/"
    pb = scraper.parse_game(read(pb_url), pb_url)
    assert "7-1792649959" in pb.cover_url and pb.metascore == 88


def test_database_url_uses_psycopg2():
    from app.db import _sqlalchemy_url
    assert _sqlalchemy_url("postgres://u:p@h:5432/db") == "postgresql+psycopg2://u:p@h:5432/db"
    assert _sqlalchemy_url("postgresql://u:p@h:5432/db?sslmode=require") == "postgresql+psycopg2://u:p@h:5432/db?sslmode=require"
    assert _sqlalchemy_url("postgresql+psycopg2://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert _sqlalchemy_url("sqlite:///./data/app.db") == "sqlite:///./data/app.db"


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
    names = sorted({p["platform"] for g in games for p in g["platforms"]})
    assert len(names) >= 2
    both = c.get("/api/games", params=[("platform", names[0]), ("platform", names[1])]).json()
    assert both and all(any(p["platform"] in names[:2] for p in g["platforms"]) for g in both)
    page = c.get("/", params=[("platform", names[0]), ("platform", names[1])]).text
    assert 'class="platform-menu"' in page and "change from:.platform-menu input" in page
    assert f'value="{names[0]}"' in page and "checked" in page
    assert f"{names[0]}, {names[1]}" in page and "«Все платформы»" in c.get("/").text
    assert c.get("/").status_code == 200 and c.get(f"/games/{games[0]['id']}").status_code == 200
    from app.db import SessionLocal
    from app.models import Game
    with SessionLocal() as s:
        row = s.get(Game, games[0]["id"])
        row.letsplay_status = "error: <HttpError 429 when requesting https://www.googleapis.com/youtube/v3/search quota>"
        s.commit()
    page = c.get(f"/games/{games[0]['id']}").text
    assert "исчерпан дневной лимит YouTube API" in page
    assert "googleapis" not in page and "HttpError" not in page
    with SessionLocal() as s:
        row = s.get(Game, games[0]["id"])
        row.letsplay_status = "error: boom"
        s.commit()
    assert "Не удалось разобрать летсплей" in c.get(f"/games/{games[0]['id']}").text
    assert c.get("/monitor").status_code == 200
    # second run same day must not re-take new releases
    from app.db import SessionLocal
    from app.models import DayProgress
    with SessionLocal() as s:
        assert s.get(DayProgress, ingest.today()).new_releases_done
