import logging
from urllib.parse import urlparse

import requests

from app.config import settings

logger = logging.getLogger(__name__)


def telegram_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def _webhook_url() -> str | None:
    if not settings.backend_url:
        return None
    return f"{settings.backend_url.rstrip('/')}/telegram/webhook"


def _is_public_webhook_base(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https":
        return False
    return host not in {"localhost", "127.0.0.1", "0.0.0.0"}


def ensure_webhook_configured():
    if not settings.telegram_bot_token:
        return

    webhook_url = _webhook_url()
    if not webhook_url:
        logger.warning("Telegram webhook setup skipped because BACKEND_URL is empty.")
        return

    if not _is_public_webhook_base(webhook_url):
        logger.warning(
            "Telegram webhook is not public. Set BACKEND_URL to an HTTPS public URL, "
            "for example an ngrok URL, so Telegram can deliver approvals. Current value: %s",
            settings.backend_url,
        )
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
    response = requests.post(url, json={"url": webhook_url}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram setWebhook failed: {payload}")
    logger.info("Telegram webhook configured: %s", webhook_url)


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
