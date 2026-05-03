import TaskCard from "./TaskCard.jsx";

export default function TaskList({ tasks, selectedId, onSelect }) {
  return (
    <div className="list">
      {tasks.map((task) => (
        <TaskCard
          key={task.id}
          task={task}
          selected={task.id === selectedId}
          onClick={() => onSelect(task.id)}
        />
      ))}
      {!tasks.length && <div className="empty small">No tasks yet.</div>}
    </div>
  );
}
