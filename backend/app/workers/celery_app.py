from celery import Celery

from app.config import settings

celery_app = Celery(
    "agent_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.agent_worker"],
)

celery_app.conf.update(task_track_started=True)
