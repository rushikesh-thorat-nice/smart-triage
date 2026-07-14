import asyncio
import json
from typing import Any


def _safe_print(s: str) -> None:
    """Print that survives Windows cp1252 stdout — replaces non-encodable
    characters instead of crashing the whole request."""
    try:
        print(s, flush=True)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode("ascii"), flush=True)


class Notifier:
    """Fan-out event bus for the dashboard.

    Every connected WebSocket has its own queue; events are broadcast to all
    active queues. Console output is also emitted for demo visibility.

    Two channels:
      - `emit(event)` → the main incidents WS (feed / metrics / approvals)
      - `emit_term(incident_id, level, text)` → the investigator terminal WS
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []
        self._term_queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def subscribe_term(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._term_queues.append(q)
        return q

    def unsubscribe_term(self, q: asyncio.Queue) -> None:
        if q in self._term_queues:
            self._term_queues.remove(q)

    async def emit(self, event: dict[str, Any]) -> None:
        line = self._format_console(event)
        if line:
            _safe_print(line)
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def emit_term(self, incident_id: int, level: str, text: str) -> None:
        """Push a line to the investigator terminal WS + console."""
        event = {"type": "term", "incident_id": incident_id, "level": level, "text": text}
        _safe_print(f"[term#{incident_id}] {level}: {text}")
        for q in list(self._term_queues):
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
