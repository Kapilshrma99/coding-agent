export function connectTaskSocket(onMessage) {
  const url = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/tasks";
  const socket = new WebSocket(url);
  socket.onmessage = (event) => onMessage(JSON.parse(event.data));
  return socket;
}
