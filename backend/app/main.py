import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routes import tasks, telegram
from app.services.websocket_manager import manager, redis_event_listener
from app.services.ollama_service import ensure_model_available
from app.services.telegram_service import ensure_webhook_configured

app = FastAPI(title="AI Agent Approval Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(telegram.router)


@app.on_event("startup")
async def on_startup():
    init_db()
    ensure_model_available()
    ensure_webhook_configured()
    asyncio.create_task(redis_event_listener())


@app.get("/health")
def health():
    return {"ok": True}


@app.websocket("/ws/tasks")
async def task_updates(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
