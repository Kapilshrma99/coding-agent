import asyncio
import json
from typing import Any

from fastapi import WebSocket
from redis import Redis
from redis.exceptions import RedisError

from app.config import settings

CHANNEL = "task_events"


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]):
        stale: list[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


manager = ConnectionManager()
_event_loop: asyncio.AbstractEventLoop | None = None


def configure_event_loop(loop: asyncio.AbstractEventLoop):
    global _event_loop
    _event_loop = loop


def redis_enabled() -> bool:
    return bool(settings.redis_url.strip())


def _publish_local(message: dict[str, Any]):
    if _event_loop and _event_loop.is_running():
        asyncio.run_coroutine_threadsafe(manager.broadcast(message), _event_loop)


def publish_task_event(task_id: int, status: str, event: str = "task_updated", **payload: Any):
    message = {"event": event, "task_id": task_id, "status": status, **payload}
    if not redis_enabled():
        _publish_local(message)
        return

    redis = Redis.from_url(settings.redis_url)
    try:
        redis.publish(CHANNEL, json.dumps(message))
    except RedisError:
        _publish_local(message)
    finally:
        redis.close()


async def redis_event_listener():
    if not redis_enabled():
        return

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    pubsub.subscribe(CHANNEL)
    try:
        while True:
            try:
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if message:
                    await manager.broadcast(json.loads(message["data"]))
            except RedisError:
                return
            await asyncio.sleep(0.1)
    finally:
        pubsub.close()
        redis.close()
