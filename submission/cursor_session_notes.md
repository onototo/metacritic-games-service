# Лог работы AI-агента (часть «переписки с нейросетью»)

Задание выполнял AI-агент (Grok Bot) по поручению Nikita Mozolev, 2026-11-05 (Europe/Minsk).
Исходный запрос пользователя: «Сделай тестовое задание и сохрани переписку с нейросетью в json».

## Ход работы
1. Прочитал ТЗ (`ai-test-task.txt`) и план со стеком/промптами (`skytec-ai-engineer-plan.md`).
2. Проверил окружение: Python 3.13 (Docker недоступен), ключей OPENAI/ANTHROPIC/YOUTUBE нет → mock-LLM.
3. Разведка Metacritic через curl: страницы отдаются (200). Нашёл селекторы: `data-testid="new-game-release-carousel"`,
   `filter-results` (browse), JSON-LD `VideoGame` (название, описание, обложка, трейлер, жанр, издатель),
   `hero-summary-developer`, `all-platforms` (metascore по платформам), `score-card-overview` на `user-reviews/?platform=` (userscore), `review-card`.
4. Написал модели (Game, GamePlatformScore, ReviewSummary, ProcessingRun, ProcessedToday, DayProgress, JobEvent), Alembic-миграцию.
5. Scraper: httpx с ретраями/backoff/троттлингом → Playwright fallback → режим fixtures; запись фикстур `SCRAPER_RECORD_FIXTURES=1`.
6. Ingest с дневной логикой (New Releases → browse по страницам), upsert, LLM-резюме critics/users, JSONL-лог каждого вызова.
7. FastAPI REST + Jinja2/HTMX UI, похожие игры, /monitor c SSE и кнопкой trigger, APScheduler (in-process и отдельный worker).
8. Первый живой прогон: 13/20 ок, 7 ошибок — баг парсинга `Metascore tbd`. Исправил, второй прогон: 20/20 (browse p1).
9. Записал 100+ HTML-фикстур из живых запросов; pytest: 5 passed (парсеры, mock-LLM+JSONL, seed-run, API, UI).
10. Сервис поднят локально на :8000, планировщик сделал третий (scheduled) прогон; сделаны скриншоты UI.
11. Исправлен показ обложек (`referrerpolicy=no-referrer`), написаны README/SUBMISSION, экспорт `submission/ai_conversation.json`.

## Решения
* Jinja2+HTMX вместо Next.js — быстрее и один процесс.
* Резюме обновляются при каждом повторном обходе игры (upsert ReviewSummary по (game_id, source)).
* Если ключей нет — не выдумываю их; mock-провайдер логируется так же, как реальный (`provider: mock`).

## Update 2026-11-05 13:30 Europe/Minsk
- Providers: Groq (`openai/gpt-oss-20b`) preferred; Gemini/YouTube keys supported via `.env`.
- Docker Compose on desktop: api+worker+postgres; UI theme toggle + favicon.
- LLM conversation export refreshed from live `data/llm_logs` (keys redacted).
- Do not commit `.env`; demo uses env/secrets on host.
