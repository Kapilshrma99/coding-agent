from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import TaskStatus


class TaskCreate(BaseModel):
    title: str
    prompt: str


class TaskLogRead(BaseModel):
    id: int
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskRead(BaseModel):
    id: int
    title: str
    prompt: str
    status: TaskStatus
    result: str | None
    summary: str | None
    created_at: datetime
    updated_at: datetime
    logs: list[TaskLogRead] = []

    model_config = ConfigDict(from_attributes=True)
