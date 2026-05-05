import json
import requests
from pathlib import Path
import time
import logging
from collections.abc import Callable

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
MAX_CONTEXT_CHARS = 12000
MIN_CONTEXT_CHARS = 1200
APPROX_CHARS_PER_TOKEN = 3
PROMPT_SAFETY_MARGIN_CHARS = 1200
MAX_FILE_SEGMENT_CHARS = 1800
MAX_SUMMARY_BATCH_CHARS = 6000
MAX_SUMMARY_TEXT_CHARS = 8000

CONTEXT_SUMMARY_SYSTEM_PROMPT = """
You are preparing compact repository notes for another coding agent.
Summarize the provided code snippets faithfully.

Rules:
- Preserve file paths.
- Call out exported functions, classes, routes, schemas, config, and side effects.
- Mention relationships between files when obvious.
- Do not invent behavior that is not shown.
- Keep the summary dense and technical.
"""


def _summary_model_name() -> str:
    return settings.ollama_summary_model.strip() or settings.ollama_model


def ensure_model_available(
    model_name: str | None = None,
    on_log: Callable[[str], None] | None = None,
):
    """Ensure the requested Ollama model is available before any requests."""
    max_retries = 60
    retry_count = 0
    resolved_model = model_name or settings.ollama_model
    
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
            
            if any(resolved_model in name for name in model_names):
                logger.info("Model %s is available", resolved_model)
                if on_log:
                    on_log(f"Model {resolved_model} is available in Ollama.")
                return True
            
            # Pull the model
            logger.info("Pulling model %s...", resolved_model)
            if on_log:
                on_log(f"Pulling model {resolved_model} into Ollama.")
            pull_response = requests.post(
                f"{settings.ollama_url}/api/pull",
                json={"name": resolved_model},
                timeout=(
                    settings.ollama_connect_timeout_seconds,
                    settings.ollama_pull_timeout_seconds,
                ),
            )
            pull_response.raise_for_status()
            logger.info("Model %s pulled successfully", resolved_model)
            if on_log:
                on_log(f"Model {resolved_model} pulled successfully.")
            return True
            
        except requests.exceptions.RequestException as e:
            retry_count += 1
            if retry_count >= max_retries:
                logger.error(f"Failed to reach Ollama after {max_retries} retries: {e}")
                if on_log:
                    on_log(f"Failed to reach Ollama after {max_retries} retries: {e}")
                raise
            logger.info(f"Ollama not ready yet (attempt {retry_count}/{max_retries}), retrying in 2s...")
            if on_log:
                on_log(f"Ollama not ready yet (attempt {retry_count}/{max_retries}), retrying in 2s.")
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


def _collect_context_segments() -> list[str]:
    root = _repo_root()
    segments: list[str] = []

    for path in _iter_context_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        stripped = content.strip()
        if not stripped:
            continue

        if len(stripped) <= MAX_FILE_SEGMENT_CHARS:
            segments.append(f"--- {relative} ---\n{stripped}\n")
            continue

        start = 0
        segment_index = 1
        while start < len(stripped):
            part = stripped[start : start + MAX_FILE_SEGMENT_CHARS]
            segments.append(
                f"--- {relative} (segment {segment_index}) ---\n{part}\n"
            )
            start += MAX_FILE_SEGMENT_CHARS
            segment_index += 1

    return segments


def build_code_context(max_chars: int | None = None) -> str:
    chunks: list[str] = []
    total = 0
    char_limit = max_chars if max_chars is not None else MAX_CONTEXT_CHARS

    for chunk in _collect_context_segments():
        if total + len(chunk) > char_limit:
            break
        chunks.append(chunk)
        total += len(chunk)

    return "".join(chunks).strip()


def _compute_context_budget(prefix: str, suffix: str) -> int:
    max_prompt_chars = settings.ollama_num_ctx * APPROX_CHARS_PER_TOKEN
    budget = max_prompt_chars - len(prefix) - len(suffix) - PROMPT_SAFETY_MARGIN_CHARS
    return max(0, min(MAX_CONTEXT_CHARS, budget))


def _chunk_text_blocks(blocks: list[str], char_limit: int) -> list[str]:
    batches: list[str] = []
    current: list[str] = []
    current_len = 0

    for block in blocks:
        if current and current_len + len(block) > char_limit:
            batches.append("\n".join(current).strip())
            current = []
            current_len = 0
        current.append(block)
        current_len += len(block)

    if current:
        batches.append("\n".join(current).strip())

    return batches


def _summarize_context_segments(
    segments: list[str],
    context_budget: int,
    on_log: Callable[[str], None] | None = None,
) -> str:
    if not segments or context_budget <= 0:
        return "Repository context omitted to fit the model context window."

    batches = _chunk_text_blocks(segments, MAX_SUMMARY_BATCH_CHARS)
    summaries: list[str] = []
    if on_log:
        on_log(
            f"Repository context is too large for one prompt. Summarizing {len(segments)} segments in {len(batches)} batch(es)."
        )

    for index, batch in enumerate(batches, start=1):
        if on_log:
            on_log(
                f"Starting repository context summary batch {index}/{len(batches)} ({len(batch)} chars)."
            )
        summary_prompt = (
            f"{CONTEXT_SUMMARY_SYSTEM_PROMPT}\n\n"
            f"Repository snippet batch {index} of {len(batches)}:\n{batch}\n\n"
            "Repository notes:"
        )
        summary = _stream_generate_response(
            prompt=summary_prompt,
            request_name=f"context_summary_{index}",
            on_log=on_log,
            model_name=_summary_model_name(),
            num_ctx=settings.ollama_summary_num_ctx,
        )
        if summary:
            summaries.append(f"[Batch {index}]\n{summary.strip()}")
            if on_log:
                on_log(
                    f"Finished repository context summary batch {index}/{len(batches)} with {len(summary)} chars."
                )

    if not summaries:
        return "Repository context omitted to fit the model context window."

    combined = "\n\n".join(summaries)
    if len(combined) <= context_budget:
        return combined

    condensed_prompt = (
        f"{CONTEXT_SUMMARY_SYSTEM_PROMPT}\n\n"
        "Condense these repository notes into one compact digest for a coding agent.\n"
        "Keep all important file paths, APIs, and architecture details.\n\n"
        f"{combined[:MAX_SUMMARY_TEXT_CHARS]}\n\n"
        "Compact digest:"
    )
    if on_log:
        on_log(
            f"Condensing {len(combined)} chars of repository notes into a compact digest."
        )
    condensed = _stream_generate_response(
        prompt=condensed_prompt,
        request_name="context_summary_condensed",
        on_log=on_log,
        model_name=_summary_model_name(),
        num_ctx=settings.ollama_summary_num_ctx,
    ).strip()
    if condensed:
        if on_log:
            on_log(f"Repository digest ready with {len(condensed)} chars.")
        return condensed[:context_budget]
    return combined[:context_budget]


def _build_prompt_with_context(
    prefix: str,
    suffix: str,
    on_log: Callable[[str], None] | None = None,
) -> str:
    context_budget = _compute_context_budget(prefix, suffix)
    all_segments = _collect_context_segments()
    raw_context = ""
    if context_budget >= MIN_CONTEXT_CHARS:
        chunks: list[str] = []
        total = 0
        for segment in all_segments:
            if total + len(segment) > context_budget:
                break
            chunks.append(segment)
            total += len(segment)
        raw_context = "".join(chunks).strip()
    context = raw_context
    if context_budget >= MIN_CONTEXT_CHARS and all_segments and not raw_context:
        context = _summarize_context_segments(all_segments, context_budget, on_log=on_log)
    elif context_budget >= MIN_CONTEXT_CHARS:
        total_segment_chars = sum(len(segment) for segment in all_segments)
        if total_segment_chars > context_budget:
            logger.info(
                "Repository context exceeds budget (%s > %s), summarizing in chunks",
                total_segment_chars,
                context_budget,
            )
            if on_log:
                on_log(
                    f"Repository context exceeds budget ({total_segment_chars} > {context_budget}); switching to chunked summaries."
                )
            context = _summarize_context_segments(all_segments, context_budget, on_log=on_log)
    if not context:
        context = "Repository context omitted to fit the model context window."
    prompt = f"{prefix}{context}{suffix}"
    logger.info(
        "Prepared Ollama prompt with %s chars of repository context (budget=%s, total=%s)",
        len(context),
        context_budget,
        len(prompt),
    )
    if on_log:
        on_log(
            f"Prepared final prompt with {len(prompt)} chars and {len(context)} chars of repository context."
        )
    return prompt


def call_ollama(
    prompt: str,
    on_chunk: Callable[[str, str], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> dict[str, str]:
    prompt_text = _build_prompt_with_context(
        prefix=f"{SYSTEM_PROMPT}\n\nRepository context:\n",
        suffix=f"\n\nUser task:\n{prompt}",
        on_log=on_log,
    )
    raw_response = _stream_generate_response(
        prompt=prompt_text,
        response_format="json",
        request_name="task_generation",
        on_chunk=on_chunk,
        on_log=on_log,
    )
    parsed = _parse_agent_response(raw_response)
    return {
        "result": parsed["result"],
        "summary": parsed["summary"],
        "actions": parsed["actions"],
        "raw_response": raw_response,
    }


def chat_about_code(question: str) -> str:
    prompt = _build_prompt_with_context(
        prefix=f"{CODE_CHAT_SYSTEM_PROMPT}\n\nRepository context:\n",
        suffix=f"\n\nTelegram user question:\n{question}\n\nAnswer:",
    )
    response_text = _stream_generate_response(
        prompt=prompt,
        request_name="code_chat",
    )
    return response_text or "I could not generate an answer."


def ask_about_task(task, question: str) -> str:
    logs = "\n".join(
        f"- {log.created_at.isoformat()}: {log.message}" for log in getattr(task, "logs", [])
    ) or "No logs yet."
    prompt = _build_prompt_with_context(
        prefix=f"{TASK_CHAT_SYSTEM_PROMPT}\n\nRepository context:\n",
        suffix=(
            f"\n\nTask details:\n"
            f"ID: {task.id}\n"
            f"Title: {task.title}\n"
            f"Status: {task.status.value}\n"
            f"Prompt:\n{task.prompt}\n\n"
            f"Summary:\n{task.summary or 'No summary yet.'}\n\n"
            f"Result:\n{task.result or 'No result yet.'}\n\n"
            f"Task logs:\n{logs}\n\n"
            f"User question about this task:\n{question}\n\n"
            "Answer:"
        ),
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
    on_chunk: Callable[[str, str], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    model_name: str | None = None,
    num_ctx: int | None = None,
) -> str:
    resolved_model = model_name or settings.ollama_model
    resolved_num_ctx = num_ctx or settings.ollama_num_ctx
    ensure_model_available(model_name=resolved_model, on_log=on_log)

    payload: dict[str, object] = {
        "model": resolved_model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "num_ctx": resolved_num_ctx,
        },
    }
    if response_format:
        payload["format"] = response_format

    logger.info("Starting Ollama streamed generate request: %s", request_name)
    if on_log:
        on_log(
            f"Starting model request '{request_name}' on {resolved_model} with num_ctx={resolved_num_ctx} and prompt size {len(prompt)} chars."
        )
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
        started_at = time.monotonic()
        last_progress_log_at = started_at
        chunk_count = 0
        total_chars = 0

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
                    chunk_count += 1
                    total_chars += len(response_part)
                    if on_chunk:
                        on_chunk(response_part, "".join(parts))
                    now = time.monotonic()
                    if on_log and now - last_progress_log_at >= 1:
                        elapsed = now - started_at
                        on_log(
                            f"Model request '{request_name}' streaming for {elapsed:.1f}s: {total_chars} chars across {chunk_count} chunk(s)."
                        )
                        last_progress_log_at = now

                if chunk.get("done") is True:
                    logger.info("Ollama streamed generate request complete: %s", request_name)
                    if on_log:
                        elapsed = time.monotonic() - started_at
                        eval_count = chunk.get("eval_count")
                        prompt_eval_count = chunk.get("prompt_eval_count")
                        on_log(
                            f"Model request '{request_name}' completed in {elapsed:.1f}s with {total_chars} chars, prompt_eval_count={prompt_eval_count}, eval_count={eval_count}."
                        )
        except requests.exceptions.ReadTimeout as exc:
            logger.error(
                "Ollama stream timed out for %s after %s seconds with %s chars collected",
                request_name,
                settings.ollama_read_timeout_seconds,
                len("".join(parts)),
            )
            if on_log:
                on_log(
                    f"Model request '{request_name}' timed out after {settings.ollama_read_timeout_seconds}s with {total_chars} chars collected."
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
