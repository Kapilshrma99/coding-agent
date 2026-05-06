from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./backend/agent_app.db"
    redis_url: str = ""
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_summary_model: str = ""
    ollama_num_ctx: int = 8192
    ollama_summary_num_ctx: int = 4096
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    code_context_root: str = "."
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    agent_workspace_root: str = "./backend/agent_runs"
    task_execution_mode: str = "local"
    agent_max_actions: int = 8
    agent_command_timeout_seconds: int = 20
    ollama_connect_timeout_seconds: int = 10
    ollama_read_timeout_seconds: int = 900
    ollama_pull_timeout_seconds: int = 3600

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
