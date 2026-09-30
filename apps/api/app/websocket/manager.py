from fastapi import WebSocket, WebSocketDisconnect

# Roles that monitor every station and session (web admin / technician dashboards).
_MONITOR_ROLES = {"ADMIN", "TECHNICIAN"}


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self._identities: dict[WebSocket, dict] = {}

    async def connect(self, websocket: WebSocket, user: dict | None = None) -> None:
        await websocket.accept()
        self.connections.add(websocket)
        if user is not None:
            self._identities[websocket] = {
                "user_id": user["user_id"],
                "role": user.get("role"),
            }

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)
        self._identities.pop(websocket, None)

    def _can_receive(self, websocket: WebSocket, data: dict) -> bool:
        owner = data.get("user_id")
        if owner is None:
            return True

        identity = self._identities.get(websocket)
        if identity is None:
            return False

        return identity["role"] in _MONITOR_ROLES or identity["user_id"] == owner

    async def broadcast(self, data: dict) -> None:
        dead: list[WebSocket] = []
        for websocket in list(self.connections):
            if not self._can_receive(websocket, data):
                continue
            try:
                await websocket.send_json(data)
            except (RuntimeError, WebSocketDisconnect):
                dead.append(websocket)

        for websocket in dead:
            self.disconnect(websocket)


manager = ConnectionManager()
