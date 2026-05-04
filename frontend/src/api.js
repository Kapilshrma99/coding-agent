const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Request failed");
  }
  return response.json();
}

export const api = {
  listTasks: () => request("/api/tasks"),
  getTask: (id) => request(`/api/tasks/${id}`),
  createTask: (payload) =>
    request("/api/tasks", { method: "POST", body: JSON.stringify(payload) }),
  approveTask: (id) => request(`/api/tasks/${id}/approve`, { method: "POST" }),
  rejectTask: (id) => request(`/api/tasks/${id}/reject`, { method: "POST" }),
};
