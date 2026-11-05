"""Standalone worker process (docker 'worker' service): hourly APScheduler job."""
import logging
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from .config import settings
from .db import init_db
from . import ingest

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    init_db()
    sch = BlockingScheduler(timezone=settings.tz)
    sch.add_job(ingest.run_once, "interval", minutes=settings.schedule_minutes, kwargs={"trigger": "scheduled"},
                max_instances=1, coalesce=True, next_run_time=datetime.now())
    sch.start()
