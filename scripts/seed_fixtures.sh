#!/usr/bin/env bash
# Наполняет БД из HTML fixtures (без сети) — для демо, если Metacritic блокирует.
set -euo pipefail
cd "$(dirname "$0")/.."
SCRAPER_USE_FIXTURES=1 PYTHONPATH=. ${PYTHON:-python} -m app.cli seed
