"""Metacritic scraper: httpx fast path -> Playwright fallback -> fixtures mode."""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .config import settings

log = logging.getLogger(__name__)
BASE = "https://www.metacritic.com"
NEW_RELEASES_URL = f"{BASE}/game/"
BROWSE_URL = f"{BASE}/browse/game/all/all/all-time/new/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class BlockedError(Exception):
    pass


def fixture_name(url: str) -> str:
    path = url.replace(BASE, "")
    name = re.sub(r"[^a-zA-Z0-9-]+", "_", path).strip("_") or "root"
    return name + ".html"


class Fetcher:
    def __init__(self, use_fixtures: bool | None = None, record: bool | None = None):
        self.use_fixtures = settings.scraper_use_fixtures if use_fixtures is None else use_fixtures
        self.record = (os.getenv("SCRAPER_RECORD_FIXTURES") == "1") if record is None else record
        self._client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)
        self._pw = None
        self._browser = None
        self._last = 0.0

    def close(self):
        self._client.close()
        if self._browser:
            self._browser.close()
            self._pw.stop()

    def _throttle(self):
        wait = settings.request_delay_sec - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _fetch_httpx(self, url):
        r = self._client.get(url)
        if r.status_code in (403, 429, 503) or "Access Denied" in r.text[:2000]:
            raise BlockedError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.text

    def _fetch_playwright(self, url):
        if not self._browser:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        ctx = self._browser.new_context(user_agent=UA, locale="en-US")
        try:
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if resp and resp.status in (403, 429, 503):
                raise BlockedError(f"PW HTTP {resp.status}")
            return page.content()
        finally:
            ctx.close()

    def get(self, url: str) -> str:
        if self.use_fixtures:
            path = os.path.join(settings.fixtures_dir, fixture_name(url))
            if not os.path.exists(path):
                raise FileNotFoundError(f"fixture missing: {path}")
            with open(path, encoding="utf-8") as f:
                return f.read()
        last_err = None
        for attempt in range(settings.request_retries):
            self._throttle()
            try:
                if settings.scraper_force_playwright or (attempt > 0 and isinstance(last_err, BlockedError)):
                    html = self._fetch_playwright(url)
                else:
                    html = self._fetch_httpx(url)
                if self.record:
                    os.makedirs(settings.fixtures_dir, exist_ok=True)
                    with open(os.path.join(settings.fixtures_dir, fixture_name(url)), "w", encoding="utf-8") as f:
                        f.write(html)
                return html
            except Exception as e:  # noqa
                last_err = e
                log.warning("fetch %s attempt %s failed: %s", url, attempt + 1, e)
                time.sleep(2 ** attempt)
        raise last_err


# ---------------- parsers ----------------

def _game_links(scope) -> list[str]:
    out = []
    for a in scope.select('a[href^="/game/"]'):
        href = a.get("href", "")
        if re.fullmatch(r"/game/[^/]+/", href) and href not in out:
            out.append(href)
    return [urljoin(BASE, h) for h in out]


def parse_new_releases(html: str) -> list[str]:
    s = BeautifulSoup(html, "lxml")
    scope = s.select_one('[data-testid="new-game-release-carousel"]')
    if scope is None:
        h = s.find(string=re.compile("New Releases"))
        scope = h.find_parent("section") if h else s
    return _game_links(scope)


def parse_browse(html: str) -> list[str]:
    s = BeautifulSoup(html, "lxml")
    cards = s.select('[data-testid="filter-results"]')
    links = []
    for c in cards:
        for u in _game_links(c):
            if u not in links:
                links.append(u)
    return links


def browse_url(page: int) -> str:
    return BROWSE_URL if page <= 1 else f"{BROWSE_URL}?page={page}"


@dataclass
class Review:
    text: str
    score: float | None = None
    author: str | None = None
    platform: str | None = None


@dataclass
class PlatformInfo:
    name: str
    slug: str | None
    metascore: int | None = None
    critic_reviews_count: int | None = None
    userscore: float | None = None


@dataclass
class GameData:
    url: str
    slug: str
    title: str
    cover_url: str | None = None
    developer: str | None = None
    publisher: str | None = None
    genres: list = field(default_factory=list)
    release_date: str | None = None
    description: str | None = None
    video_url: str | None = None
    metascore: int | None = None
    userscore: float | None = None
    platforms: list[PlatformInfo] = field(default_factory=list)
    critic_reviews: list[Review] = field(default_factory=list)
    user_reviews: list[Review] = field(default_factory=list)


def _num(t):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def parse_reviews(scope) -> list[Review]:
    out = []
    if scope is None:
        return out
    for card in scope.select('[data-testid="review-card"]'):
        q = card.select_one('[data-testid="review-quote-text"]')
        if not q:
            continue
        hdr = card.select_one('[data-testid="review-card-header"]')
        parts = hdr.get_text("|", strip=True).split("|") if hdr else []
        score = next((_num(p) for p in parts if _num(p) is not None), None)
        author = next((p for p in parts if _num(p) is None and not re.match(r"[A-Z][a-z]{2} \d", p)), None)
        plat = card.select_one('[data-testid="review-platform"]')
        out.append(Review(text=q.get_text(" ", strip=True), score=score, author=author,
                          platform=plat.get_text(strip=True) if plat else None))
    return out


def _ld_image_url(image):
    """JSON-LD image may be a URL string, ImageObject, or a list of either."""
    if isinstance(image, str):
        image = image.strip()
        return image or None
    if isinstance(image, dict):
        return _ld_image_url(image.get("url")) or _ld_image_url(image.get("contentUrl"))
    if isinstance(image, list):
        for item in image:
            url = _ld_image_url(item)
            if url:
                return url
    return None


def _catalog_img_url(img) -> str | None:
    src = (img.get("src") or "").strip()
    if "catalog/provider" in src:
        return src
    srcset = (img.get("srcset") or "").strip()
    if srcset:
        token = srcset.split(",")[0].strip().split()[0]
        if "catalog/provider" in token:
            return token
    return None


def _dom_cover_url(soup) -> str | None:
    for sel in (
        '[data-testid="hero-images"] img',
        '[data-testid="hero-image"] img',
        'img[src*="catalog/provider"]',
    ):
        for img in soup.select(sel):
            url = _catalog_img_url(img)
            if url:
                return url
    return None


def parse_game(html: str, url: str) -> GameData:
    s = BeautifulSoup(html, "lxml")
    ld = {}
    for sc in s.select('script[type="application/ld+json"]'):
        try:
            d = json.loads(sc.string or "{}")
            if d.get("@type") == "VideoGame":
                ld = d
                break
        except Exception:
            pass
    slug = url.rstrip("/").split("/")[-1]
    title_el = s.select_one('[data-testid="hero-title"]')
    g = GameData(url=url, slug=slug, title=ld.get("name") or (title_el.get_text(strip=True) if title_el else slug))
    g.description = ld.get("description")
    g.cover_url = _ld_image_url(ld.get("image")) or _dom_cover_url(s)
    g.release_date = ld.get("datePublished")
    gen = ld.get("genre")
    g.genres = gen if isinstance(gen, list) else ([gen] if gen else [])
    pubs = ld.get("publisher") or []
    g.publisher = ", ".join(p.get("name", "") for p in pubs if isinstance(p, dict)) or None
    tr = ld.get("trailer") or {}
    g.video_url = tr.get("embedUrl") or tr.get("contentUrl")
    if not g.video_url:
        m = re.search(r"cdn\.jwplayer\.com/v2/media/([A-Za-z0-9]+)/poster", html)
        if m:
            g.video_url = f"https://cdn.jwplayer.com/players/{m.group(1)}.html"
    agg = ld.get("aggregateRating") or {}
    g.metascore = int(agg["ratingValue"]) if agg.get("ratingValue") is not None else None
    dev = s.select_one('[data-testid="hero-summary-developer"]')
    if dev:
        names = [x.get_text(strip=True) for x in dev.select("span.text-gray-800")]
        g.developer = ", ".join(names) or dev.get_text(" ", strip=True).replace("Developer:", "").strip()
    scores = [x.get_text(strip=True) for x in s.select('[data-testid="global-score"]')]
    for v in scores:
        n = _num(v)
        if n is not None and n <= 10 and "." in v:
            g.userscore = n
            break
    ap = s.select_one('[data-testid="all-platforms"]')
    if ap:
        for a in ap.select("a"):
            titles = [t.get("title") for t in a.select("[title]")]
            name = next((t for t in titles if t and not t.startswith("Metascore")), None)
            if not name:
                continue
            ms = re.search(r"platform=([a-z0-9-]+)", a.get("href", ""))
            mscore = next((int(m.group()) for t in titles if t and t.startswith("Metascore")
                           for m in [re.search(r"\d+", t)] if m), None)
            cnt = re.search(r"Based on ([\d,]+) Critic", a.get_text(" ", strip=True))
            g.platforms.append(PlatformInfo(name=name, slug=ms.group(1) if ms else None, metascore=mscore,
                                            critic_reviews_count=int(cnt.group(1).replace(",", "")) if cnt else None))
    if not g.platforms:
        for p in ld.get("gamePlatform") or []:
            g.platforms.append(PlatformInfo(name=p, slug=re.sub(r"[^a-z0-9]+", "-", p.lower()).strip("-")))
        if len(g.platforms) == 1:
            g.platforms[0].metascore = g.metascore
    g.critic_reviews = parse_reviews(s.select_one('[data-testid="critic-reviews"]'))
    g.user_reviews = parse_reviews(s.select_one('[data-testid="user-reviews"]'))
    return g


def parse_user_reviews_page(html: str) -> tuple[float | None, list[Review]]:
    s = BeautifulSoup(html, "lxml")
    ov = s.select_one('[data-testid="score-card-overview"]')
    us = None
    if ov:
        first = ov.get_text("|", strip=True).split("|")[0]
        us = _num(first)
    return us, parse_reviews(s)


def scrape_game(fetcher: Fetcher, url: str) -> GameData:
    g = parse_game(fetcher.get(url), url)
    seen = {r.text for r in g.user_reviews}
    for p in g.platforms[: settings.max_platform_pages]:
        if not p.slug:
            continue
        try:
            us, revs = parse_user_reviews_page(fetcher.get(f"{url}user-reviews/?platform={p.slug}"))
        except Exception as e:  # noqa
            log.info("user reviews %s %s unavailable: %s", url, p.slug, e)
            continue
        p.userscore = us
        for r in revs:
            if r.text not in seen:
                seen.add(r.text)
                g.user_reviews.append(r)
    try:
        crit = parse_reviews(BeautifulSoup(fetcher.get(f"{url}critic-reviews/"), "lxml"))
        known = {r.text for r in g.critic_reviews}
        g.critic_reviews += [r for r in crit if r.text not in known]
    except Exception as e:  # noqa
        log.info("critic reviews page %s unavailable: %s", url, e)
    return g
