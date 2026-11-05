# Metacritic Games Service

Тестовое задание AI Automation Engineer (Skytec Games). Сервис раз в час обходит Metacritic Games, берёт до 20 игр,
которые **сегодня** ещё не обрабатывались, сохраняет/обновляет их в БД, делает LLM-резюме отзывов критиков и игроков,
подбирает похожие игры и показывает всё в веб-интерфейсе с live-мониторингом.

## Стек
Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL (SQLite fallback) · httpx + Playwright · BeautifulSoup ·
APScheduler · pydantic-settings · Jinja2 + HTMX · SSE · pytest · Docker Compose.

## Логика выборки (TZ = Europe/Minsk)
* `DayProgress(day)` хранит состояние дня: `new_releases_done`, `next_browse_page`.
* Первый проход дня → карусель **New Releases** на https://www.metacritic.com/game/ (до 20 игр).
* Следующие проходы → https://www.metacritic.com/browse/game/all/all/all-time/new/?page=N; берём ещё не обработанные
  сегодня игры, страница считается исчерпанной → следующий запуск продолжает со следующей.
* `ProcessedToday(url, day)` — дедуп «сегодня уже обработано». Новый день → новая запись `DayProgress` → снова New Releases.
* Если игра уже есть в БД — данные (оценки, платформы, резюме) обновляются (upsert по `metacritic_url`).

## Что собирается
Название, обложка, платформы + Metascore (по каждой платформе, блок *All Platforms*) + Userscore (по каждой платформе
со страницы `user-reviews/?platform=`), разработчик, издатель, жанр, дата релиза, описание, ссылка на трейлер (JW Player
из JSON-LD), отзывы критиков (`critic-reviews/`) и игроков (по платформам).

## LLM
`app/llm.py`, порядок auto: **Groq** (`GROQ_API_KEY`) → Gemini (`GEMINI_API_KEY`) → OpenAI → Anthropic → **mock**
(эвристика по оценкам/аспектам). Рекомендуемый бесплатный старт: Groq + модель `openai/gpt-oss-20b`.
Без ключей сервис всё равно поднимается: резюме будут mock, YouTube off.
**Каждый вызов** → `data/llm_logs/YYYY-MM-DD.jsonl`. Агрегат: `submission/ai_conversation.json` (`scripts/export_submission.sh`).

## Похожие игры
`app/similar.py`: взвешенный Jaccard по жанрам (0.4), платформам (0.2), разработчику (0.15) и токенам описания (0.25) — только
по играм из своей БД; в карточке кликабельны.

## Веб-интерфейс
* `/` — список (обложка, оценки, платформы), поиск по названию, фильтр платформ, сортировка по Metascore/Userscore (HTMX, без перезагрузки).
* `/games/{id}` — карточка: все поля, таблица платформ, резюме критиков/игроков, летсплей, похожие игры.
* `/monitor` — доп.2: статусы воркеров, прогресс N/20, счётчики, история запусков, live-лог через SSE, кнопка «Запустить сейчас».
* `/docs` — Swagger. API: `GET /api/games?q=&platform=&sort=`, `GET /api/games/{id}`, `GET /api/platforms`,
  `GET /api/runs`, `GET /api/status`, `GET /api/events/stream` (SSE), `POST /api/runs/trigger` (заголовок `X-Admin-Token`, если задан `ADMIN_TOKEN`), `GET /health`.

## Доп.1 YouTube
`app/youtube.py`: YouTube Data API (`order=viewCount`, «<title> let's play») → транскрипт `youtube-transcript-api` → LLM-заключение
(`purpose=letsplay_conclusion`). Включается `ENABLE_YOUTUBE=1` + `YOUTUBE_API_KEY`; без ключа фича выключена, статус виден в карточке.

## Запуск
### Локально (SQLite)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
cp .env.example .env
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. uvicorn app.main:app --port 8000      # планировщик внутри, первый запуск сразу
# открыть http://localhost:8000
```
Без сети / при блокировке: `./scripts/seed_fixtures.sh` (БД из HTML-фикстур `tests/fixtures/`), либо `SCRAPER_USE_FIXTURES=1`.
Разовый прогон: `PYTHONPATH=. python -m app.cli run`.

### Docker Compose (PostgreSQL + api + worker) — основной путь
```bash
cp .env.example .env
# опционально: GROQ_API_KEY=...  ENABLE_YOUTUBE=1  YOUTUBE_API_KEY=...
docker compose up --build -d
# UI http://localhost:8000  · health /health  · monitor /monitor
```
Ключи не обязательны для первого старта. `worker` — APScheduler раз в час; `api` — UI/API и ручной trigger.
Порт `8000` должен быть свободен.

### Тесты
`pytest -q` — парсеры на фикстурах, mock-LLM + JSONL, полный seed-run + API (фильтр, сортировка, карточка, monitor).

## Limitations
* Без API-ключей резюме идут через **mock**; для реальных текстов достаточно `GROQ_API_KEY` (или Gemini/OpenAI/Anthropic) в `.env`.
* YouTube включается `ENABLE_YOUTUBE=1` + `YOUTUBE_API_KEY`. Транскрипты иногда блокируются IP датацентра — тогда статус `transcript_unavailable`.
* Бесплатные квоты Groq/Gemini дают `429` при бурсте; в клиенте есть backoff, но массовый пересчёт лучше с паузой.
* Metacritic на момент разработки отдавал HTML обычному httpx-запросу; Playwright включается как fallback при 403/429
  (или принудительно `SCRAPER_FORCE_PLAYWRIGHT=1`). Вёрстка Metacritic (data-testid) может меняться.
* Для игр без отзывов/с `tbd` поля остаются пустыми. Отзывы — первая страница (~50 пользовательских на платформу, до 5 платформ).
* Lock от параллельных запусков — внутри процесса; в compose ручной trigger (api) и расписание (worker) теоретически
  могут пересечься (дедуп по ProcessedToday защищает данные).
* Публичный деплой не выполнен — сервис поднят локально.
