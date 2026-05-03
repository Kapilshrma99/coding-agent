import { useState } from "react";
import { Send } from "lucide-react";

export default function TaskForm({ onCreate }) {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!title.trim() || !prompt.trim()) return;
    setBusy(true);
    try {
      await onCreate({ title, prompt });
      setTitle("");
      setPrompt("");
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
      <button disabled={busy}>
        <Send size={17} />
        {busy ? "Queueing" : "Create Task"}
      </button>
    </form>
  );
}
