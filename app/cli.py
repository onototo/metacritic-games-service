"""CLI: python -m app.cli run|seed|export"""
import json
import glob
import os
import sys
from datetime import datetime

from .db import init_db
from . import ingest
from .config import settings


def export():
    rows = []
    for f in sorted(glob.glob(os.path.join(settings.llm_logs_dir, "*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            rows += [json.loads(l) for l in fh if l.strip()]
    os.makedirs("submission", exist_ok=True)
    out = {"meta": {"generated_at": datetime.now().isoformat(), "records": len(rows),
                    "providers": sorted({r.get("provider") for r in rows}),
                    "purposes": sorted({r.get("purpose") for r in rows}),
                    "source_files": sorted(glob.glob(os.path.join(settings.llm_logs_dir, "*.jsonl"))),
                    "note": "Все вызовы LLM сервиса (append-only JSONL, агрегировано). provider=mock — эвристика без API-ключа."},
           "records": rows}
    with open("submission/ai_conversation.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"exported {len(rows)} records -> submission/ai_conversation.json")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    init_db()
    if cmd == "run":
        print("run id", ingest.run_once(trigger="cli"))
    elif cmd == "seed":  # fill DB from HTML fixtures, no network
        print("seed run id", ingest.run_once(trigger="seed", use_fixtures=True))
    elif cmd == "export":
        export()
