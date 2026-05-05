import React, { useState } from "react";
import { Send } from "lucide-react";

export default function TaskForm({ onCreate }) {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [contextPath, setContextPath] = useState("");
  const [pastedContext, setPastedContext] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!title.trim() || !prompt.trim()) return;
    setBusy(true);
    try {
      await onCreate({
        title,
        prompt,
        context_path: contextPath.trim() || null,
        pasted_context: pastedContext.trim() || null,
      });
      setTitle("");
      setPrompt("");
      setContextPath("");
      setPastedContext("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <label>
        Title
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label>
        Task
        <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={6} />
      </label>
      <label>
        Context Path
        <input
          value={contextPath}
          onChange={(event) => setContextPath(event.target.value)}
          placeholder="Optional path inside the repo, for example backend/app/services"
        />
      </label>
      <label>
        Pasted Code Or Context
        <textarea
          value={pastedContext}
          onChange={(event) => setPastedContext(event.target.value)}
          rows={8}
          placeholder="Optional pasted code or notes to send to the model"
        />
      </label>
      <button disabled={busy}>
        <Send size={17} />
        {busy ? "Queueing" : "Create Task"}
      </button>
    </form>
  );
}
