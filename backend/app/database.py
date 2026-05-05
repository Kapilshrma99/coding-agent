from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_task_columns()


def _ensure_task_columns():
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("tasks")}
    statements: list[str] = []
    if "context_path" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN context_path TEXT")
    if "pasted_context" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN pasted_context TEXT")
    if "llm_prompt" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN llm_prompt TEXT")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
