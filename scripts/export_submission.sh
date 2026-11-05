#!/usr/bin/env bash
# Собирает артефакты сдачи: агрегированный JSON всех LLM-вызовов + копию raw JSONL.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
PYTHONPATH=. $PY -m app.cli export
mkdir -p submission/llm_logs_raw
cp data/llm_logs/*.jsonl submission/llm_logs_raw/ 2>/dev/null || true
echo "submission/ ready:"; ls -la submission
