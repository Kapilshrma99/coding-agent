# AI Agent Approval Assistant

A self-hosted full-stack MVP where a Python agent runs locally, uses a local Ollama model, sends a Telegram approval request, and waits for human approval before completing.

## Local Setup

1. Create and activate a Python virtual environment inside `backend`.

2. Install backend dependencies:

```bash
pip install -r backend/requirements.txt
```

3. Install frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

4. Copy `.env.example` to `.env` if needed and adjust values.

5. Make sure Ollama is installed and running on your machine, then pull the default model:

```bash
ollama pull qwen2.5-coder:7b
```

6. Start the backend:

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

7. Start the frontend in a second terminal:

```bash
cd frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

The dashboard runs at `http://localhost:5173` and the API runs at `http://localhost:8000`.

## Defaults

The project now runs locally by default with:

- `SQLite` at `backend/agent_app.db`
- local task execution with no Celery worker required
- optional `Redis` only if you want external pub/sub behavior
- local Ollama at `http://localhost:11434`

If you want to switch back to a queue-based setup later, set:

```bash
TASK_EXECUTION_MODE=celery
REDIS_URL=redis://localhost:6379/0
```

Suggested choices:

- `qwen2.5-coder:7b` for a balanced coding assistant.
- `deepseek-coder:6.7b` for stronger coding on machines with enough RAM.
- `qwen2.5-coder:3b` for lower RAM machines.

The app does not auto-select between models. You choose one in `.env`.

## Telegram Setup

1. Create a bot with Telegram BotFather and copy the bot token.
2. Put the token in `.env`:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
```

3. Get your chat ID by sending a message to your bot, then opening:

```text
https://api.telegram.org/botYOUR_TOKEN/getUpdates
```

4. Put the chat ID in `.env`:

```bash
TELEGRAM_CHAT_ID=your_chat_id
```

5. Expose your backend publicly for Telegram webhooks. For local testing, use a tunnel such as ngrok:

```bash
ngrok http 8000
```

6. Set the webhook:

```bash
curl "https://api.telegram.org/botYOUR_TOKEN/setWebhook?url=https://YOUR_PUBLIC_URL/telegram/webhook"
```

If Telegram is not configured, tasks still work and can be approved or rejected from the dashboard.

## Create A Task

Use the dashboard form at `http://localhost:5173`, or call the API:

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Draft plan\",\"prompt\":\"Create a safe implementation plan for a small Python CLI.\"}"
```

The task lifecycle is:

```text
pending -> running -> waiting_approval -> approved/rejected -> completed/stopped
```

## Approve Or Reject

When the worker reaches `waiting_approval`, it sends a Telegram message with inline buttons:

- Approve
- Reject

You can also approve from the dashboard or API:

```bash
curl -X POST http://localhost:8000/api/tasks/1/approve
curl -X POST http://localhost:8000/api/tasks/1/reject
```

## Live Dashboard Updates

FastAPI publishes task status changes through Redis and broadcasts them over WebSocket at:

```text
ws://localhost:8000/ws/tasks
```

The React dashboard subscribes automatically and refreshes the task list whenever a status changes. In default local mode, this works without Redis.

## Safety Behavior

- The agent only generates a proposed result.
- It does not auto-deploy.
- It does not delete files.
- It does not run destructive shell commands.
- It waits for human approval before final completion.
- Every major action is stored in task logs.
