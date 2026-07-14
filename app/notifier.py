import asyncio
import json
from typing import Any


class Notifier:
    """Fan-out event bus for the dashboard.

    Every connected WebSocket has its own queue; events are broadcast to all
    active queues. Console output is also emitted for demo visibility.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    async def emit(self, event: dict[str, Any]) -> None:
        line = self._format_console(event)
        if line:
            print(line, flush=True)
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @staticmethod
    def _format_console(event: dict[str, Any]) -> str:
        t = event.get("type", "?")
        inc = event.get("incident") or {}
        if t == "incident":
            status = inc.get("status", "?")
            product = inc.get("product", "?")
            team = inc.get("owner_team", "?")
            log = (inc.get("log_line") or "")[:100]
            return f"[triage] status={status} product={product} team={team} :: {log}"
        if t == "approval_result":
            return f"[approval] incident #{inc.get('id')} → {inc.get('status')}"
        return f"[event] {json.dumps(event, default=str)[:200]}"


notifier = Notifier()
