import os

from celery import Celery


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/2")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

celery_app = Celery(
    "openscribe",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_always_eager=_env_flag("CELERY_TASK_ALWAYS_EAGER", default=False),
    task_eager_propagates=True,
    timezone="UTC",
    beat_schedule={
        "delete-expired-transcripts-every-10-seconds": {
            "task": "openscribe.delete_expired_transcripts",
            "schedule": 10.0,
            "options": {"expires": 10.0},
        },
    },
)

celery_app.autodiscover_tasks(["app"])
