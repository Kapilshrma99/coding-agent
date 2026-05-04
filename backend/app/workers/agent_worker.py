import time

from celery.utils.log import get_task_logger

from app.database import SessionLocal
from app.models import Task, TaskLog, TaskStatus
from app.services.ollama_service import call_ollama
from app.services.telegram_service import send_approval_message, telegram_configured
from app.services.websocket_manager import publish_task_event
from app.workers.celery_app import celery_app

logger = get_task_logger(__name__)


def add_log(db, task: Task, message: str):
    db.add(TaskLog(task_id=task.id, message=message))
    db.commit()
    db.refresh(task)


def set_status(db, task: Task, status: TaskStatus, message: str):
    task.status = status
    db.commit()
    add_log(db, task, message)
    publish_task_event(task.id, status.value)


@celery_app.task(name="run_agent_task")
def run_agent_task(task_id: int):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task:
            logger.warning("Task %s not found", task_id)
            return

        set_status(db, task, TaskStatus.running, "Worker started task.")
        add_log(db, task, "Calling Ollama for proposed result.")

        try:
            output = call_ollama(task.prompt)
        except Exception as exc:
            task.summary = "Ollama call failed."
            task.result = str(exc)
            set_status(db, task, TaskStatus.stopped, f"Ollama failed: {exc}")
            return

        task.result = output["result"]
        task.summary = output["summary"]
        db.commit()
        add_log(db, task, "Ollama generated a proposed result and summary.")

        set_status(
            db,
            task,
            TaskStatus.waiting_approval,
            "Waiting for human approval before final completion.",
        )

        if telegram_configured():
            try:
                send_approval_message(task.id, task.title, task.summary or "")
                add_log(db, task, "Telegram approval message sent.")
            except Exception as exc:
                add_log(db, task, f"Telegram send failed: {exc}")
        else:
            add_log(db, task, "Telegram is not configured. Use dashboard approval buttons.")

        while True:
            db.refresh(task)
            if task.status == TaskStatus.approved:
                set_status(db, task, TaskStatus.completed, "Approved by human. Task completed.")
                return
            if task.status == TaskStatus.rejected:
                set_status(db, task, TaskStatus.stopped, "Rejected by human. Task stopped.")
                return
            time.sleep(3)
    finally:
        db.close()
