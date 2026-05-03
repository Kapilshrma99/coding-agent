import requests

from app.config import settings


def telegram_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send_approval_message(task_id: int, title: str, summary: str):
    if not telegram_configured():
        return

    text = (
        f"AI Agent Approval Required\n\n"
        f"Task #{task_id}: {title}\n\n"
        f"Summary:\n{summary}\n\n"
        "Approve to allow final completion, or reject to stop the task."
    )
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": f"approve:{task_id}"},
                    {"text": "Reject", "callback_data": f"reject:{task_id}"},
                ]
            ]
        },
    }
    requests.post(url, json=payload, timeout=20).raise_for_status()


def send_message(chat_id: str | int, text: str):
    if not settings.telegram_bot_token:
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [""]
    for chunk in chunks:
        requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=20,
        ).raise_for_status()


def answer_callback(callback_query_id: str, text: str):
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery"
    requests.post(
        url,
        json={"callback_query_id": callback_query_id, "text": text},
        timeout=10,
    ).raise_for_status()
