import time
import re
from datetime import datetime, timezone

from celery.utils.log import get_task_logger

from app.database import SessionLocal
from app.models import Task, TaskLog, TaskStatus
from app.services.ollama_service import call_ollama
from app.services.agent_runtime import execute_actions
from app.services.telegram_service import send_approval_message, telegram_configured
from app.services.websocket_manager import publish_task_event
from app.workers.celery_app import celery_app

logger = get_task_logger(__name__)


def add_log(db, task: Task, message: str):
    log = TaskLog(task_id=task.id, message=message)
    db.add(log)
    db.commit()
    db.refresh(log)
    db.refresh(task)
    publish_task_event(
        task.id,
        task.status.value,
        event="task_log",
        log={
            "id": log.id,
            "message": log.message,
            "created_at": _isoformat(log.created_at),
        },
    )


def set_status(db, task: Task, status: TaskStatus, message: str):
    task.status = status
    db.commit()
    publish_task_event(task.id, status.value)
    add_log(db, task, message)


@celery_app.task(name="run_agent_task")
def run_agent_task(task_id: int):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task:
            logger.warning("Task %s not found", task_id)
            return

        set_status(db, task, TaskStatus.running, "Worker started task.")
        add_log(db, task, "Calling Ollama for task plan.")
        task.result = ""
        db.commit()
        publish_task_event(task.id, task.status.value, event="llm_stream_started")

        last_saved_length = 0

        def handle_llm_chunk(chunk: str, full_text: str):
            nonlocal last_saved_length
            publish_task_event(
                task.id,
                task.status.value,
                event="llm_chunk",
                chunk=chunk,
            )
            if len(full_text) - last_saved_length < 500:
                return
            task.result = full_text
            db.commit()
            last_saved_length = len(full_text)
            publish_task_event(
                task.id,
                task.status.value,
                event="task_partial_result",
                result=full_text,
            )

        def handle_llm_log(message: str):
            add_log(db, task, f"LLM: {message}")

        try:
            output = call_ollama(
                task.prompt,
                context_path=task.context_path,
                pasted_context=task.pasted_context,
                on_chunk=handle_llm_chunk,
                on_log=handle_llm_log,
            )
        except Exception as exc:
            task.summary = "Ollama call failed."
            task.result = str(exc)
            db.commit()
            set_status(db, task, TaskStatus.stopped, f"Ollama failed: {exc}")
            return

        task.llm_prompt = str(output.get("prompt_text") or "")
        db.commit()
        publish_task_event(
            task.id,
            task.status.value,
            event="task_prompt",
            llm_prompt=task.llm_prompt,
        )
        add_log(db, task, f"Captured full LLM prompt ({len(task.llm_prompt)} chars).")

        publish_task_event(
            task.id,
            task.status.value,
            event="llm_stream_completed",
            result=output.get("raw_response", ""),
        )

        actions = _ensure_minimum_actions(
            task.prompt,
            output.get("actions", []),
            output.get("result", ""),
            output.get("raw_response", ""),
        )
        if actions != output.get("actions", []):
            add_log(
                db,
                task,
                "Model returned no usable file action. Added fallback workspace file actions.",
            )

        execution = execute_actions(task.id, actions)
        execution_log = "\n".join(execution["logs"])
        workspace = execution["workspace"]
        workspace_tree = execution["workspace_tree"]
        no_workspace_changes = workspace_tree == "(empty)"
        result_parts = [output["result"].strip() or "Agent completed the task."]
        result_parts.append(f"Workspace: {workspace}")
        if no_workspace_changes:
            result_parts.append("Workspace files: none created or modified")
            result_parts.append(
                "Actual outcome: no guarded workspace actions produced files. "
                "If the request was meant to create a file, reject this run and retry."
            )
        else:
            result_parts.append(f"Workspace files:\n{workspace_tree}")
            first_file = workspace_tree.splitlines()[0].strip()
            if first_file:
                result_parts.append(f"Created file location: {workspace}/{first_file}")
        result_parts.append(f"Execution log:\n{execution_log}")

        task.result = "\n\n".join(result_parts)
        task.summary = _build_summary(output["summary"], workspace_tree)
        db.commit()
        publish_task_event(
            task.id,
            task.status.value,
            event="task_result",
            result=task.result,
            summary=task.summary,
        )
        add_log(db, task, "Agent generated a result and executed guarded workspace actions.")
        for entry in execution["logs"]:
            add_log(db, task, entry)
        if no_workspace_changes:
            add_log(
                db,
                task,
                "No workspace files were created or modified by the approved action plan.",
            )

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


def _build_summary(summary: str, workspace_tree: str) -> str:
    if workspace_tree == "(empty)":
        files_line = (
            "Workspace files:\n(empty)\n\n"
            "Actual outcome: no files were created or modified in the guarded workspace."
        )
    else:
        files_line = f"Workspace files:\n{workspace_tree}"
    combined = f"{summary}\n\n{files_line}".strip()
    return combined[:700] + ("..." if len(combined) > 700 else "")


def _isoformat(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _ensure_minimum_actions(
    prompt: str,
    actions: list[dict] | None,
    result: str,
    raw_response: str,
) -> list[dict]:
    normalized_actions = [action for action in (actions or []) if isinstance(action, dict)]
    if normalized_actions:
        return normalized_actions

    recovered_code_actions = _recover_code_actions(prompt, raw_response or result)
    if recovered_code_actions:
        return recovered_code_actions

    prompt_lower = prompt.lower()
    create_file_intent = (
        "create" in prompt_lower
        and "file" in prompt_lower
        and ("text file" in prompt_lower or ".txt" in prompt_lower)
    )
    if not create_file_intent:
        return normalized_actions

    filename = _infer_text_filename(prompt)
    return [
        {
            "type": "write_file",
            "path": filename,
            "content": "",
        }
    ]


def _infer_text_filename(prompt: str) -> str:
    explicit_name = re.search(r'([A-Za-z0-9_.-]+\.txt)\b', prompt)
    if explicit_name:
        return explicit_name.group(1)

    quoted_name = re.search(r'"([^"]+)"', prompt)
    if quoted_name:
        candidate = quoted_name.group(1).strip().replace("\\", "/").split("/")[-1]
        if candidate and not candidate.endswith(".txt"):
            candidate = f"{candidate}.txt"
        if candidate:
            return candidate

    return "example.txt"


def _recover_code_actions(prompt: str, response_text: str) -> list[dict]:
    prompt_lower = prompt.lower()
    code_intent = any(
        token in prompt_lower
        for token in (
            "write code",
            "create code",
            "build",
            "implement",
            "script",
            "python file",
            "javascript file",
            "typescript file",
            ".py",
            ".js",
            ".ts",
            ".html",
            ".css",
            ".json",
        )
    )
    if not code_intent:
        return []

    code_blocks = re.findall(r"```([^\n`]*)\n(.*?)```", response_text, flags=re.DOTALL)
    actions: list[dict] = []
    if code_blocks:
        for index, (block_info, content) in enumerate(code_blocks, start=1):
            path = _infer_code_filename(prompt, block_info.strip(), index, len(code_blocks))
            actions.append(
                {
                    "type": "write_file",
                    "path": path,
                    "content": content.rstrip() + "\n",
                }
            )
        return actions

    inferred_code_path = _infer_code_filename(prompt, "", 1, 1)
    if inferred_code_path:
        return [
            {
                "type": "write_file",
                "path": inferred_code_path,
                "content": _build_placeholder_code(prompt, inferred_code_path),
            }
        ]

    return []


def _infer_code_filename(prompt: str, block_info: str, index: int, total_blocks: int) -> str:
    explicit_name = re.search(
        r'([A-Za-z0-9_/.-]+\.(?:py|js|ts|tsx|jsx|html|css|json|md|yml|yaml|toml))\b',
        prompt,
        flags=re.IGNORECASE,
    )
    if explicit_name:
        return explicit_name.group(1).replace("\\", "/")

    if block_info:
        info_name = re.search(
            r'([A-Za-z0-9_/.-]+\.(?:py|js|ts|tsx|jsx|html|css|json|md|yml|yaml|toml))\b',
            block_info,
            flags=re.IGNORECASE,
        )
        if info_name:
            return info_name.group(1).replace("\\", "/")

    block_language = block_info.split()[0].lower() if block_info else ""
    extension_map = {
        "python": "py",
        "py": "py",
        "javascript": "js",
        "js": "js",
        "typescript": "ts",
        "ts": "ts",
        "tsx": "tsx",
        "jsx": "jsx",
        "html": "html",
        "css": "css",
        "json": "json",
        "yaml": "yaml",
        "yml": "yml",
        "toml": "toml",
        "markdown": "md",
        "md": "md",
    }
    if block_language in extension_map:
        stem = "main" if total_blocks == 1 else f"file_{index}"
        return f"{stem}.{extension_map[block_language]}"

    prompt_lower = prompt.lower()
    if "python" in prompt_lower:
        return "main.py"
    if "typescript" in prompt_lower:
        return "main.ts"
    if "javascript" in prompt_lower:
        return "main.js"
    if "html" in prompt_lower:
        return "index.html"
    if "css" in prompt_lower:
        return "styles.css"
    if "json" in prompt_lower:
        return "data.json"

    return "main.py"


def _build_placeholder_code(prompt: str, path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix == "py":
        return (
            '"""Generated placeholder file."""\n\n'
            "def main() -> None:\n"
            f'    print("TODO: implement task: {prompt.strip()}")\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n"
        )
    if suffix in {"js", "ts"}:
        return f'console.log("TODO: implement task: {prompt.strip()}");\n'
    if suffix == "html":
        return (
            "<!doctype html>\n"
            "<html>\n"
            "  <head>\n"
            "    <meta charset=\"utf-8\" />\n"
            "    <title>Generated File</title>\n"
            "  </head>\n"
            "  <body>\n"
            f"    <p>TODO: implement task: {prompt.strip()}</p>\n"
            "  </body>\n"
            "</html>\n"
        )
    if suffix == "json":
        return '{\n  "todo": "implement requested task"\n}\n'
    return f"TODO: implement task: {prompt.strip()}\n"
