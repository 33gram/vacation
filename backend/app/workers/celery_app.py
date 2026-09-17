from celery import Celery

from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()

settings = get_settings()

celery_app = Celery(
    "vacation_tracker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.timezone = settings.tz
celery_app.conf.task_default_queue = "default"
# Celery hijacks the root logger on worker startup and replaces our JSON
# handlers with its own formatting unless this is disabled.
celery_app.conf.worker_hijack_root_logger = False
