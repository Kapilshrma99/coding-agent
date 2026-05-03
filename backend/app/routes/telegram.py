from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task, TaskLog, TaskStatus
from app.config import settings
from app.services.ollama_service import chat_about_code
from app.services.telegram_service import answer_callback, send_message
from app.services.websocket_manager import publish_task_event

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    payload = await request.json()
    callback = payload.get("callback_query")
    if not callback:
        message = payload.get("message") or payload.get("edited_message")
        if message:
            background_tasks.add_task(handle_chat_message, message)
        return {"ok": True}

    callback_id = callback.get("id")
    data = callback.get("data", "")
    action, _, task_id_raw = data.partition(":")
    if action not in {"approve", "reject"} or not task_id_raw.isdigit():
        if callback_id:
            answer_callback(callback_id, "Unknown action")
        return {"ok": True}

    task = db.get(Task, int(task_id_raw))
    if not task:
        if callback_id:
            answer_callback(callback_id, "Task not found")
        return {"ok": True}

    if task.status != TaskStatus.waiting_approval:
        if callback_id:
            answer_callback(callback_id, f"Task is {task.status.value}")
        return {"ok": True}

    if action == "approve":
        task.status = TaskStatus.approved
        message = "Task approved from Telegram."
    else:
        task.status = TaskStatus.rejected
        message = "Task rejected from Telegram."

    db.add(TaskLog(task_id=task.id, message=message))
    db.commit()
    publish_task_event(task.id, task.status.value)

    if callback_id:
        answer_callback(callback_id, message)
    return {"ok": True}


def handle_chat_message(message: dict):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if settings.telegram_chat_id and str(chat_id) != str(settings.telegram_chat_id):
        send_message(chat_id, "This bot is only enabled for the configured chat.")
        return

    if text.startswith("/start") or text.startswith("/help"):
        send_message(
            chat_id,
            "Send me any question about the code and I will answer from the repository context.",
        )
        return

    send_message(chat_id, "Reading the code and thinking...")
    try:
        answer = chat_about_code(text)
    except Exception as exc:
        answer = f"I hit an error while reading the code or calling Ollama: {exc}"

    send_message(chat_id, answer)
