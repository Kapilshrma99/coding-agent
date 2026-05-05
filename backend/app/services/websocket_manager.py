import asyncio
import json
from typing import Any

from fastapi import WebSocket
from redis import Redis

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


def publish_task_event(task_id: int, status: str, event: str = "task_updated", **payload: Any):
    redis = Redis.from_url(settings.redis_url)
    message = {"event": event, "task_id": task_id, "status": status, **payload}
    redis.publish(CHANNEL, json.dumps(message))
    redis.close()


async def redis_event_listener():
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    pubsub.subscribe(CHANNEL)
    try:
        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
            if message:
                await manager.broadcast(json.loads(message["data"]))
            await asyncio.sleep(0.1)
    finally:
        pubsub.close()
        redis.close()
