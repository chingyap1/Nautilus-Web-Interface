"""
Shared application state: NautilusTradingSystem singleton + WebSocket manager.
Imported by all routers so they all operate on the same engine instance.
"""

import os
from pathlib import Path

from fastapi import WebSocket

backend_dir = Path(__file__).parent
catalog_path = str(backend_dir.parent / "nautilus_data" / "catalog")
os.environ.setdefault("NAUTILUS_CATALOG_PATH", catalog_path)

from nautilus_core import NautilusTradingSystem  # noqa: E402

nautilus_system = NautilusTradingSystem(catalog_path=catalog_path)


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self.active_connections: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, owner_id: str) -> None:
        await websocket.accept()
        self.active_connections[websocket] = owner_id

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.pop(websocket, None)

    async def _send(self, connections: list[WebSocket], message: dict) -> None:
        disconnected = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast(self, message: dict) -> None:
        await self._send(list(self.active_connections), message)

    async def send_to(self, owner_id: str, message: dict) -> None:
        await self._send(
            [
                connection
                for connection, connection_owner in self.active_connections.items()
                if connection_owner == owner_id
            ],
            message,
        )


manager = ConnectionManager()

from live_trading import LiveTradingManager  # noqa: E402

live_manager = LiveTradingManager()
