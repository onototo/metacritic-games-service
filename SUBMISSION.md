# Сдача тестового: AI Automation Engineer

* **Репозиторий:** (загрузить на GitHub и вставить ссылку) — локально `metacritic-games-service/`
* **Сервис:** (после деплоя) — локально `http://localhost:8000` (`/`, `/games/{id}`, `/monitor`, `/docs`)
* **Переписка с нейросетью:**
  * raw JSONL всех LLM-вызовов сервиса: `data/llm_logs/*.jsonl` (копия в `submission/llm_logs_raw/`)
  * агрегат: `submission/ai_conversation.json`
  * лог работы AI-агента над кодом: `submission/cursor_session_notes.md`
* Скриншоты: `submission/screen_*.png`

## Выполнено
- [x] Обязательная часть: hourly-обход, 20 игр/запуск, New Releases → browse по страницам, сброс по дням (Europe/Minsk), upsert
- [x] Все поля: название, обложка, платформы + metascore + userscore, разработчик, описание, видео
- [x] Резюме отзывов критиков и игроков отдельно, обновляются при повторных обходах
- [x] UI: список, карточка, фильтр платформ, поиск, сортировка, похожие игры (кликабельны)
- [x] Доп.2: /monitor — статусы воркеров, прогресс, счётчики, SSE live-лог, кнопка принудительного запуска
- [~] Доп.1: YouTube letsplay → транскрипт → заключение реализовано, выключено без `YOUTUBE_API_KEY`
- [x] Docker Compose (Postgres + api + worker), Alembic, pytest, fixtures/seed-режим

Ограничения — см. README → Limitations.
