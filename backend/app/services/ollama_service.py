import json
import requests
from pathlib import Path
import time
import logging

from app.config import settings
from app.services.agent_runtime import ALLOWED_COMMANDS

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = f"""
You are an AI coding agent running in a guarded workspace.
You may create files, update files, inspect files, and run a limited set of
safe terminal commands, but only inside the task workspace assigned to you.
You must not delete files, access parent directories, deploy anything, or run
destructive shell commands.

Return strict JSON with this shape:
{{
  "summary": "short human summary",
  "result": "what you built or discovered",
  "actions": [
    {{"type": "write_file", "path": "main.py", "content": "print('hi')"}},
    {{"type": "append_file", "path": "notes.txt", "content": "\\nmore"}},
    {{"type": "read_file", "path": "main.py"}},
    {{"type": "list_files"}},
    {{"type": "run_command", "command": ["python", "main.py"]}}
  ]
}}

Rules:
- Respond with exactly one JSON object and no markdown fences, headings, or prose outside JSON.
- Paths must be relative, never absolute.
- Only use the action types listed above.
- Allowed commands are: {", ".join(sorted(ALLOWED_COMMANDS))}
- Prefer creating files in the workspace rather than describing code abstractly.
- If the task asks you to create or modify a file, include the matching file action.
- If the task asks for code, return the code inside one or more write_file actions instead of only describing it in result text.
- Create every necessary source file explicitly. For example, if a task needs `main.py` and `requirements.txt`, include two write_file actions.
- If no action is needed, return an empty actions list.
"""

CODE_CHAT_SYSTEM_PROMPT = """
You are a friendly senior coding assistant chatting through Telegram.
Answer naturally and directly about this codebase. Use the supplied repository
context when it helps, say when you are uncertain, and keep answers concise
enough for Telegram. Do not claim you changed files or ran commands.
"""

TASK_CHAT_SYSTEM_PROMPT = """
You are a friendly senior coding assistant helping a human review one task.
Answer questions about the task's prompt, current status, summary, result,
workspace artifacts, and logs. Use the repository context when helpful.
Be specific, concise, and honest about uncertainty.
"""

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".md",
    ".txt",
    ".css",
    ".html",
}
IGNORED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".pytest_cache",
}
MAX_CONTEXT_CHARS = 26000
MAX_FILE_CHARS = 5000


def ensure_model_available():
    """Ensure the Ollama model is available before any requests."""
    max_retries = 60
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Check if Ollama is up
            response = requests.get(
                f"{settings.ollama_url}/api/tags",
                timeout=(settings.ollama_connect_timeout_seconds, 5),
            )
            response.raise_for_status()
            
            # Check if model is already present
            tags = response.json().get("models", [])
            model_names = [m.get("name", "") for m in tags]
            
            if any(settings.ollama_model in name for name in model_names):
                logger.info(f"Model {settings.ollama_model} is available")
                return True
            
            # Pull the model
            logger.info(f"Pulling model {settings.ollama_model}...")
            pull_response = requests.post(
                f"{settings.ollama_url}/api/pull",
                json={"name": settings.ollama_model},
                timeout=(
                    settings.ollama_connect_timeout_seconds,
                    settings.ollama_pull_timeout_seconds,
                ),
            )
            pull_response.raise_for_status()
            logger.info(f"Model {settings.ollama_model} pulled successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            retry_count += 1
            if retry_count >= max_retries:
                logger.error(f"Failed to reach Ollama after {max_retries} retries: {e}")
                raise
            logger.info(f"Ollama not ready yet (attempt {retry_count}/{max_retries}), retrying in 2s...")
            time.sleep(2)
    
    return False


def _repo_root() -> Path:
    root = Path(settings.code_context_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _iter_context_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        yield path


def build_code_context() -> str:
    root = _repo_root()
    chunks: list[str] = []
    total = 0

    for path in _iter_context_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n...[truncated]"

        chunk = f"\n--- {relative} ---\n{content.strip()}\n"
        if total + len(chunk) > MAX_CONTEXT_CHARS:
            break
        chunks.append(chunk)
        total += len(chunk)

    return "".join(chunks).strip()


def call_ollama(prompt: str) -> dict[str, str]:
    context = build_code_context()
    raw_response = _stream_generate_response(
        prompt=(
            f"{SYSTEM_PROMPT}\n\n"
            f"Repository context:\n{context or 'No repository files were readable.'}\n\n"
            f"User task:\n{prompt}"
        ),
        response_format="json",
        request_name="task_generation",
    )
    parsed = _parse_agent_response(raw_response)
    return {
        "result": parsed["result"],
        "summary": parsed["summary"],
        "actions": parsed["actions"],
        "raw_response": raw_response,
    }


def chat_about_code(question: str) -> str:
    context = build_code_context()
    prompt = (
        f"{CODE_CHAT_SYSTEM_PROMPT}\n\n"
        f"Repository context:\n{context or 'No repository files were readable.'}\n\n"
        f"Telegram user question:\n{question}\n\n"
        "Answer:"
    )
    response_text = _stream_generate_response(
        prompt=prompt,
        request_name="code_chat",
    )
    return response_text or "I could not generate an answer."


def ask_about_task(task, question: str) -> str:
    context = build_code_context()
    logs = "\n".join(
        f"- {log.created_at.isoformat()}: {log.message}" for log in getattr(task, "logs", [])
    ) or "No logs yet."
    prompt = (
        f"{TASK_CHAT_SYSTEM_PROMPT}\n\n"
        f"Repository context:\n{context or 'No repository files were readable.'}\n\n"
        f"Task details:\n"
        f"ID: {task.id}\n"
        f"Title: {task.title}\n"
        f"Status: {task.status.value}\n"
        f"Prompt:\n{task.prompt}\n\n"
        f"Summary:\n{task.summary or 'No summary yet.'}\n\n"
        f"Result:\n{task.result or 'No result yet.'}\n\n"
        f"Task logs:\n{logs}\n\n"
        f"User question about this task:\n{question}\n\n"
        "Answer:"
    )
    response_text = _stream_generate_response(
        prompt=prompt,
        request_name="task_chat",
    )
    return response_text or "I could not generate an answer."


def _stream_generate_response(
    prompt: str,
    request_name: str,
    response_format: str | None = None,
) -> str:
    ensure_model_available()

    payload: dict[str, object] = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": True,
    }
    if response_format:
        payload["format"] = response_format

    logger.info("Starting Ollama streamed generate request: %s", request_name)
    with requests.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=(
            settings.ollama_connect_timeout_seconds,
            settings.ollama_read_timeout_seconds,
        ),
        stream=True,
    ) as response:
        response.raise_for_status()
        parts: list[str] = []

        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue

                logger.info("Ollama stream chunk [%s]: %s", request_name, raw_line)

                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    logger.warning("Skipping non-JSON Ollama stream chunk [%s]", request_name)
                    continue

                response_part = chunk.get("response")
                if isinstance(response_part, str) and response_part:
                    parts.append(response_part)

                if chunk.get("done") is True:
                    logger.info("Ollama streamed generate request complete: %s", request_name)
        except requests.exceptions.ReadTimeout as exc:
            logger.error(
                "Ollama stream timed out for %s after %s seconds with %s chars collected",
                request_name,
                settings.ollama_read_timeout_seconds,
                len("".join(parts)),
            )
            raise RuntimeError(
                "Ollama generation timed out before completion. "
                f"Current read timeout is {settings.ollama_read_timeout_seconds} seconds."
            ) from exc

        return "".join(parts).strip()


def _parse_agent_response(raw_response: str) -> dict[str, str | list[dict]]:
    candidate = raw_response.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    payload = _load_json_object(candidate)
    if payload is None:
        summary = raw_response[:700] + ("..." if len(raw_response) > 700 else "")
        return {"summary": summary, "result": raw_response, "actions": []}

    if not isinstance(payload, dict):
        summary = raw_response[:700] + ("..." if len(raw_response) > 700 else "")
        return {"summary": summary, "result": raw_response, "actions": []}

    summary = str(payload.get("summary") or "") or "Agent generated a response."
    result = str(payload.get("result") or "") or "No result provided."
    actions = payload.get("actions")
    if not isinstance(actions, list):
        actions = []
    normalized_actions = [action for action in actions if isinstance(action, dict)]
    return {"summary": summary, "result": result, "actions": normalized_actions}


def _load_json_object(candidate: str) -> dict | list | None:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(candidate):
        if char not in "[{":
            continue
        try:
            payload, _end = decoder.raw_decode(candidate[index:])
            return payload
        except json.JSONDecodeError:
            continue
    return None
