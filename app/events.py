import logging
from .db import SessionLocal
from .models import JobEvent

WORKER_STATE: dict = {}


def emit(message, run_id=None, worker="ingest", level="info", **data):
    WORKER_STATE[worker] = {"status": data.get("status", WORKER_STATE.get(worker, {}).get("status", "running")),
                            "message": message, "run_id": run_id, **data}
    try:
        with SessionLocal() as s:
            s.add(JobEvent(run_id=run_id, worker=worker, level=level, message=message, data=data or None))
            s.commit()
    except Exception as e:  # monitoring must never break ingestion
        logging.getLogger(__name__).warning("emit failed: %s", e)
