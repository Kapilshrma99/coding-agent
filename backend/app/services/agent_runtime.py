import subprocess
from pathlib import Path

from app.config import settings

ALLOWED_COMMANDS = {
    "python",
    "python3",
    "pytest",
    "ls",
    "pwd",
    "cat",
}
MAX_OUTPUT_CHARS = 4000


def ensure_task_workspace(task_id: int) -> Path:
    root = Path(settings.agent_workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = (root / f"task-{task_id}").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def workspace_tree(workspace: Path) -> str:
    files = []
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            files.append(path.relative_to(workspace).as_posix())
    return "\n".join(files) or "(empty)"


def execute_actions(task_id: int, actions: list[dict]) -> dict[str, str | list[str]]:
    workspace = ensure_task_workspace(task_id)
    logs: list[str] = [f"Workspace: {workspace}"]

    for index, action in enumerate(actions[: settings.agent_max_actions], start=1):
        try:
            action_type = (action.get("type") or "").strip()
            if action_type == "write_file":
                logs.append(_write_file_action(workspace, index, action))
            elif action_type == "append_file":
                logs.append(_append_file_action(workspace, index, action))
            elif action_type == "read_file":
                logs.append(_read_file_action(workspace, index, action))
            elif action_type == "list_files":
                logs.append(_list_files_action(workspace, index))
            elif action_type == "run_command":
                logs.append(_run_command_action(workspace, index, action))
            else:
                logs.append(f"{index}. Skipped unsupported action: {action_type or 'missing type'}")
        except Exception as exc:
            logs.append(f"{index}. Action failed: {exc}")

    return {
        "workspace": str(workspace),
        "workspace_tree": workspace_tree(workspace),
        "logs": logs,
    }


def _resolve_workspace_path(workspace: Path, relative_path: str) -> Path:
    if not relative_path:
        raise ValueError("Path is required")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError("Absolute paths are not allowed")
    resolved = (workspace / candidate).resolve()
    if workspace != resolved and workspace not in resolved.parents:
        raise ValueError("Path escapes the task workspace")
    return resolved


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + "\n...[truncated]"


def _write_file_action(workspace: Path, index: int, action: dict) -> str:
    path = _resolve_workspace_path(workspace, str(action.get("path") or ""))
    content = str(action.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"{index}. Wrote file {path.relative_to(workspace).as_posix()} ({len(content)} chars)"


def _append_file_action(workspace: Path, index: int, action: dict) -> str:
    path = _resolve_workspace_path(workspace, str(action.get("path") or ""))
    content = str(action.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)
    return f"{index}. Appended to file {path.relative_to(workspace).as_posix()} ({len(content)} chars)"


def _read_file_action(workspace: Path, index: int, action: dict) -> str:
    path = _resolve_workspace_path(workspace, str(action.get("path") or ""))
    if not path.exists() or not path.is_file():
        return f"{index}. Read failed for {path.relative_to(workspace).as_posix()}: file not found"
    content = _truncate(path.read_text(encoding="utf-8", errors="replace"))
    return f"{index}. Read file {path.relative_to(workspace).as_posix()}:\n{content}"


def _list_files_action(workspace: Path, index: int) -> str:
    return f"{index}. Workspace files:\n{workspace_tree(workspace)}"


def _run_command_action(workspace: Path, index: int, action: dict) -> str:
    command = action.get("command")
    if not isinstance(command, list) or not command:
        return f"{index}. Command failed: command must be a non-empty list"

    command = [str(part) for part in command]
    if command[0] not in ALLOWED_COMMANDS:
        return f"{index}. Command blocked: {command[0]} is not allowed"
    if any(".." in part for part in command[1:]):
        return f"{index}. Command blocked: parent-directory traversal is not allowed"

    completed = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=settings.agent_command_timeout_seconds,
        check=False,
    )
    stdout = _truncate(completed.stdout.strip())
    stderr = _truncate(completed.stderr.strip())
    output_parts = [f"{index}. Ran command: {' '.join(command)}", f"Exit code: {completed.returncode}"]
    if stdout:
        output_parts.append(f"stdout:\n{stdout}")
    if stderr:
        output_parts.append(f"stderr:\n{stderr}")
    return "\n".join(output_parts)
