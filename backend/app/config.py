from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@postgres:5432/agent_db"
    redis_url: str = "redis://redis:6379/0"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_summary_model: str = ""
    ollama_num_ctx: int = 8192
    ollama_summary_num_ctx: int = 4096
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    code_context_root: str = "."
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    agent_workspace_root: str = "/app/agent_runs"
    agent_max_actions: int = 8
    agent_command_timeout_seconds: int = 20
    ollama_connect_timeout_seconds: int = 10
    ollama_read_timeout_seconds: int = 900
    ollama_pull_timeout_seconds: int = 3600

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
