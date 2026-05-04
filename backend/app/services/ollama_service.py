import requests
from pathlib import Path
import time
import logging

from app.config import settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are an AI Agent Approval Assistant running in a guarded MVP.
You may analyze and draft a result, but you must not deploy, delete files,
run destructive shell commands, or claim that a final action was performed.
Return a useful proposed result and a concise summary for human approval.
"""

CODE_CHAT_SYSTEM_PROMPT = """
You are a friendly senior coding assistant chatting through Telegram.
Answer naturally and directly about this codebase. Use the supplied repository
context when it helps, say when you are uncertain, and keep answers concise
enough for Telegram. Do not claim you changed files or ran commands.
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
                timeout=5
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
                timeout=3600  # Allow up to 1 hour for model pull
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
    response = requests.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": settings.ollama_model,
            "prompt": f"{SYSTEM_PROMPT}\n\nUser task:\n{prompt}",
            "stream": False,
        },
        timeout=180,
    )
    response.raise_for_status()
    result = response.json().get("response", "").strip()
    summary = result[:700] + ("..." if len(result) > 700 else "")
    return {"result": result, "summary": summary}


def chat_about_code(question: str) -> str:
    context = build_code_context()
    prompt = (
        f"{CODE_CHAT_SYSTEM_PROMPT}\n\n"
        f"Repository context:\n{context or 'No repository files were readable.'}\n\n"
        f"Telegram user question:\n{question}\n\n"
        "Answer:"
    )
    response = requests.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip() or "I could not generate an answer."
