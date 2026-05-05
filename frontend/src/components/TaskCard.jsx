import React from "react";

export default function TaskCard({ task, selected, onClick }) {
  const hasExplicitContext = Boolean(task.context_path || task.pasted_context);

  return (
    <button className={`taskCard ${selected ? "selected" : ""}`} onClick={onClick}>
      <span className={`badge ${task.status}`}>{task.status}</span>
      <strong>{task.title}</strong>
      <small>{hasExplicitContext ? "Explicit context attached" : "No extra context"}</small>
      <small>{new Date(task.created_at).toLocaleString()}</small>
    </button>
  );
}
