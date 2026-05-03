export default function TaskCard({ task, selected, onClick }) {
  return (
    <button className={`taskCard ${selected ? "selected" : ""}`} onClick={onClick}>
      <span className={`badge ${task.status}`}>{task.status}</span>
      <strong>{task.title}</strong>
      <small>{new Date(task.created_at).toLocaleString()}</small>
    </button>
  );
}
