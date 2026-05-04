from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Task, TaskLog, TaskStatus
from app.schemas import TaskCreate, TaskRead
from app.services.websocket_manager import publish_task_event
from app.workers.agent_worker import run_agent_task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def log_and_publish(db: Session, task: Task, message: str):
    db.add(TaskLog(task_id=task.id, message=message))
    db.commit()
    db.refresh(task)
    publish_task_event(task.id, task.status.value)


def get_task_or_404(db: Session, task_id: int) -> Task:
    task = db.execute(
        select(Task).options(selectinload(Task.logs)).where(Task.id == task_id)
    ).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("", response_model=TaskRead)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = Task(title=payload.title, prompt=payload.prompt, status=TaskStatus.pending)
    db.add(task)
    db.commit()
    db.refresh(task)
    log_and_publish(db, task, "Task created and queued.")
    run_agent_task.delay(task.id)
    return get_task_or_404(db, task.id)


@router.get("", response_model=list[TaskRead])
def list_tasks(db: Session = Depends(get_db)):
    return (
        db.execute(select(Task).options(selectinload(Task.logs)).order_by(Task.created_at.desc()))
        .scalars()
        .all()
    )


@router.get("/{task_id}", response_model=TaskRead)
def read_task(task_id: int, db: Session = Depends(get_db)):
    return get_task_or_404(db, task_id)


@router.post("/{task_id}/approve", response_model=TaskRead)
def approve_task(task_id: int, db: Session = Depends(get_db)):
    task = get_task_or_404(db, task_id)
    if task.status != TaskStatus.waiting_approval:
        raise HTTPException(status_code=409, detail="Task is not waiting for approval")
    task.status = TaskStatus.approved
    log_and_publish(db, task, "Task approved by API/dashboard.")
    return get_task_or_404(db, task.id)


@router.post("/{task_id}/reject", response_model=TaskRead)
def reject_task(task_id: int, db: Session = Depends(get_db)):
    task = get_task_or_404(db, task_id)
    if task.status != TaskStatus.waiting_approval:
        raise HTTPException(status_code=409, detail="Task is not waiting for approval")
    task.status = TaskStatus.rejected
    log_and_publish(db, task, "Task rejected by API/dashboard.")
    return get_task_or_404(db, task.id)
