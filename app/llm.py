"""LLM client with provider auto-detection and append-only JSONL logging of every call."""
import json
import os
import re
import threading
import uuid
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .config import settings

_lock = threading.Lock()

SYSTEM_PROMPT = (
    "Ты аналитик игровых отзывов. По набору отзывов ({source}) напиши короткое резюме на русском "
    "(3-5 предложений): что нравится, что не нравится, общий вывод. Без выдумок, только по тексту отзывов. "
    "Формат: 'Нравится: ...\\nНе нравится: ...\\nИтог: ...'"
)


def log_llm_call(record: dict):
    os.makedirs(settings.llm_logs_dir, exist_ok=True)
    day = datetime.now(ZoneInfo(settings.tz)).strftime("%Y-%m-%d")
    path = os.path.join(settings.llm_logs_dir, f"{day}.jsonl")
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def detect_provider() -> str:
    p = settings.llm_provider
    if p != "auto":
        return p
    if settings.groq_api_key or os.getenv("GROQ_API_KEY"):
        return "groq"
    if settings.gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    if settings.openai_api_key or os.getenv("OPENAI_API_KEY"):
        return "openai"
    if settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "mock"


# ------------- heuristic mock (no API keys) -------------
ASPECTS = {
    "геймплей": ["gameplay", "combat", "mechanic", "controls", "fun", "co-op", "coop", "puzzle", "levels", "boss"],
    "сюжет": ["story", "narrative", "writing", "characters", "plot", "dialogue", "ending"],
    "графика и арт": ["graphics", "visual", "art", "beautiful", "stunning", "animation", "looks"],
    "звук и музыка": ["music", "soundtrack", "audio", "voice", "sound"],
    "техническое состояние": ["bug", "performance", "fps", "crash", "framerate", "optimization", "glitch", "stutter", "requirements"],
    "контент и длительность": ["short", "length", "hours", "content", "replay", "long", "repetitive", "grind"],
    "цена и монетизация": ["price", "$", "microtransaction", "dlc", "expensive", "worth", "money"],
    "порт/ремастер": ["port", "remaster", "remake", "switch 2", "original"],
}
NEG = ["bad", "boring", "worst", "bug", "crash", "repetitive", "short", "expensive", "disappoint", "lack", "poor",
       "broken", "terrible", "awful", "overpriced", "stutter", "but", "however", "unfortunately"]


def _aspects(texts):
    c = Counter()
    for t in texts:
        low = t.lower()
        for a, kws in ASPECTS.items():
            if any(k in low for k in kws):
                c[a] += 1
    return [a for a, _ in c.most_common(3)]


def mock_summary(source: str, reviews: list[dict]) -> str:
    if not reviews:
        return None
    scale = 100 if source == "critics" else 10
    pos, neg = [], []
    for r in reviews:
        s = r.get("score")
        txt = r["text"]
        if s is not None:
            (pos if s >= 0.7 * scale else neg).append(txt)
        else:
            (neg if any(w in txt.lower() for w in NEG[:15]) else pos).append(txt)
    scores = [r["score"] for r in reviews if r.get("score") is not None]
    avg = sum(scores) / len(scores) if scores else None
    likes = _aspects(pos) or ["общее впечатление"]
    dislikes = _aspects(neg + [t for t in pos if any(w in t.lower() for w in NEG)]) or []
    dislikes = [d for d in dislikes if d not in likes[:1]] or dislikes
    who = "Критики" if source == "critics" else "Игроки"
    tone = ("в целом положительно" if avg is not None and avg >= 0.7 * scale else
            "неоднозначно" if avg is not None and avg >= 0.5 * scale else "скорее негативно" if avg is not None else "без явной оценки")
    quote = sorted(pos, key=len)[len(pos) // 2][:160] if pos else ""
    lines = [f"Нравится: {', '.join(likes)}." + (f" Пример: «{quote}…»" if quote else ""),
             f"Не нравится: {', '.join(dislikes) if dislikes and neg else 'серьёзных претензий почти нет'}.",
             f"Итог: {who} оценивают игру {tone}" + (f" (средняя {avg:.1f}/{scale}, {len(reviews)} отзывов, "
                                                     f"{len(pos)} позитивных / {len(neg)} критичных)." if avg is not None else ".")]
    return "\n".join(lines)


def mock_conclusion(transcript: str) -> str:
    words = re.findall(r"[a-zA-Zа-яА-Я']{5,}", transcript.lower())
    top = ", ".join(w for w, _ in Counter(words).most_common(8))
    return f"Блогер в ролике чаще всего обсуждает: {top}. (Эвристический вывод без LLM.)"


# ------------- real providers -------------
def _gemini(system, user):
    import time
    key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    model = settings.gemini_model
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 800},
    }
    # Prefer header so logs don't print the key in the URL; retry on free-tier 429.
    last_err = None
    for attempt in range(6):
        r = httpx.post(url, headers={"x-goog-api-key": key}, json=payload, timeout=60)
        if r.status_code == 429:
            wait = min(2 ** attempt, 40)
            time.sleep(wait)
            last_err = httpx.HTTPStatusError(f"429 rate limit after {attempt+1} tries", request=r.request, response=r)
            continue
        r.raise_for_status()
        raw = r.json()
        parts = raw.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        out = "".join(p.get("text", "") for p in parts)
        u = raw.get("usageMetadata", {})
        usage = {"input_tokens": u.get("promptTokenCount"), "output_tokens": u.get("candidatesTokenCount")}
        return out, usage, raw, model
    raise last_err



def _groq(system, user):
    import time
    key = settings.groq_api_key or os.getenv("GROQ_API_KEY")
    url = f"{settings.groq_base_url}/chat/completions"
    payload = {"model": settings.groq_model, "temperature": 0.3,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    last_err = None
    for attempt in range(8):
        r = httpx.post(url, timeout=60, headers={"Authorization": f"Bearer {key}"}, json=payload)
        if r.status_code == 429:
            wait = min(2 ** attempt + 2, 60)
            time.sleep(wait)
            last_err = httpx.HTTPStatusError("429 rate limit", request=r.request, response=r)
            continue
        r.raise_for_status()
        raw = r.json()
        u = raw.get("usage", {})
        return raw["choices"][0]["message"]["content"], {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}, raw, settings.groq_model
    raise last_err

def _openai(system, user):
    key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    r = httpx.post(f"{settings.openai_base_url}/chat/completions", timeout=60,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": settings.openai_model, "temperature": 0.3,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    raw = r.json()
    u = raw.get("usage", {})
    return raw["choices"][0]["message"]["content"], {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}, raw, settings.openai_model


def _anthropic(system, user):
    key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
                   headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                   json={"model": settings.anthropic_model, "max_tokens": 600, "system": system,
                         "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    raw = r.json()
    u = raw.get("usage", {})
    return raw["content"][0]["text"], {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}, raw, settings.anthropic_model


def _call(purpose: str, game_id, system: str, user: str, mock_fn, input_extra: dict):
    provider = detect_provider()
    rec = {"id": str(uuid.uuid4()), "ts": datetime.now(ZoneInfo(settings.tz)).isoformat(), "provider": provider,
           "purpose": purpose, "game_id": game_id,
           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
           "input": input_extra}
    try:
        if provider == "groq":
            out, usage, raw, model = _groq(system, user)
        elif provider == "gemini":
            out, usage, raw, model = _gemini(system, user)
        elif provider == "openai":
            out, usage, raw, model = _openai(system, user)
        elif provider == "anthropic":
            out, usage, raw, model = _anthropic(system, user)
        else:
            out = mock_fn()
            model = "heuristic-v1"
            usage = {"input_tokens": len(user.split()), "output_tokens": len((out or "").split())}
            raw = {"note": "mock provider: no GEMINI_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY in env", "output": out}
        rec.update(model=model, output=out, usage=usage, raw_response=raw, status="ok")
    except Exception as e:  # fallback to mock, but log the failure
        out = mock_fn()
        err = str(e)
        for secret in filter(None, [settings.groq_api_key, os.getenv("GROQ_API_KEY"),
                                    settings.gemini_api_key, os.getenv("GEMINI_API_KEY"), os.getenv("GOOGLE_API_KEY"),
                                    settings.openai_api_key, os.getenv("OPENAI_API_KEY"),
                                    settings.anthropic_api_key, os.getenv("ANTHROPIC_API_KEY")]):
            err = err.replace(secret, "[REDACTED]")
        rec.update(model="heuristic-v1(fallback)", output=out, usage={}, raw_response={"error": err}, status="fallback")
    log_llm_call(rec)
    return out, rec["provider"]


def summarize_reviews(source: str, reviews: list[dict], game_id=None, title="") -> tuple[str | None, str]:
    reviews = reviews[:40]
    if not reviews:
        rec = {"id": str(uuid.uuid4()), "ts": datetime.now(ZoneInfo(settings.tz)).isoformat(), "provider": "none",
               "purpose": f"review_summary_{source}", "game_id": game_id, "input": {"title": title, "reviews": []},
               "output": None, "status": "skipped_no_reviews"}
        log_llm_call(rec)
        return None, "none"
    system = SYSTEM_PROMPT.format(source="критики" if source == "critics" else "игроки")
    body = "\n".join(f"- [{r.get('score')}] {r['text'][:600]}" for r in reviews)
    user = f"Игра: {title}\nОтзывы ({len(reviews)}):\n{body}"
    return _call(f"review_summary_{source}", game_id, system, user, lambda: mock_summary(source, reviews),
                 {"title": title, "reviews_count": len(reviews)})


def letsplay_conclusion(title: str, transcript: str, game_id=None):
    system = "Ты кратко пересказываешь мнение блогера об игре по транскрипту летсплея. Ответ на русском, 3-5 предложений."
    user = f"Игра: {title}\nТранскрипт (обрезан):\n{transcript[:12000]}"
    return _call("letsplay_conclusion", game_id, system, user, lambda: mock_conclusion(transcript),
                 {"title": title, "transcript_chars": len(transcript)})
