import { useEffect, useMemo, useState } from "react";
import { Bot, RefreshCcw } from "lucide-react";
import { api } from "../api.js";
import { connectTaskSocket } from "../socket.js";
import TaskForm from "../components/TaskForm.jsx";
import TaskList from "../components/TaskList.jsx";

export default function Dashboard() {
  const [tasks, setTasks] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [error, setError] = useState("");

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedId) || tasks[0],
    [tasks, selectedId]
  );

  async function loadTasks() {
    try {
      const data = await api.listTasks();
      setTasks(data);
      setSelectedId((current) => current || data[0]?.id || null);
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    loadTasks();
    const socket = connectTaskSocket(() => loadTasks());
    return () => socket.close();
  }, []);

  async function createTask(payload) {
    const task = await api.createTask(payload);
    await loadTasks();
    setSelectedId(task.id);
  }

  async function decide(taskId, action) {
    if (action === "approve") {
      await api.approveTask(taskId);
    } else {
      await api.rejectTask(taskId);
    }
    await loadTasks();
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <Bot size={28} />
          <div>
            <h1>AI Agent Approval Assistant</h1>
            <p>Human-gated local agent runs with Telegram approval.</p>
          </div>
        </div>
        <button className="iconButton" onClick={loadTasks} aria-label="Refresh tasks">
          <RefreshCcw size={18} />
        </button>
      </header>

      {error && <div className="alert">{error}</div>}

      <section className="layout">
        <div className="leftPane">
          <TaskForm onCreate={createTask} />
          <TaskList tasks={tasks} selectedId={selectedTask?.id} onSelect={setSelectedId} />
        </div>

        <div className="detailPane">
          {selectedTask ? (
            <article className="detail">
              <div className="detailHeader">
                <div>
                  <span className={`badge ${selectedTask.status}`}>{selectedTask.status}</span>
                  <h2>{selectedTask.title}</h2>
                </div>
                {selectedTask.status === "waiting_approval" && (
                  <div className="actions">
                    <button onClick={() => decide(selectedTask.id, "approve")}>Approve</button>
                    <button className="secondary" onClick={() => decide(selectedTask.id, "reject")}>
                      Reject
                    </button>
                  </div>
                )}
              </div>
              <section>
                <h3>Prompt</h3>
                <pre>{selectedTask.prompt}</pre>
              </section>
              <section>
                <h3>Summary</h3>
                <p>{selectedTask.summary || "Waiting for model output."}</p>
              </section>
              <section>
                <h3>Result</h3>
                <pre>{selectedTask.result || "No result yet."}</pre>
              </section>
              <section>
                <h3>Logs</h3>
                <div className="logs">
                  {selectedTask.logs.map((log) => (
                    <div key={log.id} className="logLine">
                      <time>{new Date(log.created_at).toLocaleString()}</time>
                      <span>{log.message}</span>
                    </div>
                  ))}
                </div>
              </section>
            </article>
          ) : (
            <div className="empty">Create a task to start the approval flow.</div>
          )}
        </div>
      </section>
    </main>
  );
}
