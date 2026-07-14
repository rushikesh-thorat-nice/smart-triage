import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import kb, runner, triage
from .config import settings
from .db import Incident, get_session, init_db
from .ingest import tail_lines
from .notifier import notifier


STATIC_DIR = Path(__file__).parent / "static"


def _incident_to_dict(inc: Incident) -> dict:
    return {
        "id": inc.id,
        "created_at": inc.created_at.isoformat() if inc.created_at else None,
        "log_line": inc.log_line,
        "is_known": inc.is_known,
        "matched_kb_id": inc.matched_kb_id,
        "product": inc.product,
        "owner_team": inc.owner_team,
        "severity": inc.severity,
        "confidence": inc.confidence,
        "reasoning": inc.reasoning,
        "recommended_action": inc.recommended_action,
        "status": inc.status,
        "action_output": inc.action_output,
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
    }


async def _handle_log_line(line: str) -> None:
    if not triage.is_triageable(line):
        return

    try:
        decision = await asyncio.to_thread(triage.triage, line)
    except Exception as e:  # noqa: BLE001
        print(f"[triage] error: {e}", file=sys.stderr)
        return

    matched_kb = None
    if decision["is_known_issue"] and decision["matched_kb_id"]:
        matched_kb = kb.get_entry(decision["matched_kb_id"])

    # Fallback team/severity if we could not match anything.
    owner_team = matched_kb.owner_team if matched_kb else "sre-general"
    severity = matched_kb.severity if matched_kb else "unknown"
    recommended_action = matched_kb.resolution_command if matched_kb else None

    if matched_kb and matched_kb.auto_execute:
        status = "auto_resolved"
    elif matched_kb:
        status = "pending_approval"
    else:
        status = "alerted_new"

    inc = Incident(
        log_line=line,
        is_known=decision["is_known_issue"] and matched_kb is not None,
        matched_kb_id=decision["matched_kb_id"] if matched_kb else None,
        product=decision["affected_product"] or (matched_kb.product if matched_kb else "unknown"),
        owner_team=owner_team,
        severity=severity,
        confidence=float(decision["confidence"]),
        reasoning=decision["reasoning"],
        recommended_action=recommended_action,
        status=status,
    )

    with get_session() as session:
        session.add(inc)
        session.commit()
        session.refresh(inc)
        incident_id = inc.id

    if status == "auto_resolved" and recommended_action:
        result = await asyncio.to_thread(runner.execute, recommended_action)
        with get_session() as session:
            row = session.get(Incident, incident_id)
            if row:
                row.action_output = result["output"]
                row.status = "auto_resolved" if result["success"] else "failed"
                row.resolved_at = datetime.utcnow()
                session.commit()
                session.refresh(row)
                inc = row

    await notifier.emit({"type": "incident", "incident": _incident_to_dict(inc)})


async def _tail_task():
    async for line in tail_lines():
        # Fire-and-forget so a slow triage call doesn't block the tailer.
        asyncio.create_task(_handle_log_line(line))


_generator_proc: subprocess.Popen | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Ensure Chroma is warm and KB is loaded — seed if empty.
    if not kb.list_entries():
        kb.seed_from_file()

    task = asyncio.create_task(_tail_task())

    global _generator_proc
    if settings.simulate:
        _generator_proc = subprocess.Popen(
            [sys.executable, "-m", "app.generator"],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        print(f"[main] started log generator pid={_generator_proc.pid}")

    try:
        yield
    finally:
        task.cancel()
        if _generator_proc and _generator_proc.poll() is None:
            _generator_proc.terminate()
            try:
                _generator_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _generator_proc.kill()


app = FastAPI(title="Smart Triage", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/incidents")
async def list_incidents(limit: int = 100):
    with get_session() as session:
        rows = (
            session.query(Incident)
            .order_by(Incident.id.desc())
            .limit(limit)
            .all()
        )
        return [_incident_to_dict(r) for r in rows]


@app.get("/api/pending")
async def list_pending():
    with get_session() as session:
        rows = (
            session.query(Incident)
            .filter(Incident.status == "pending_approval")
            .order_by(Incident.id.desc())
            .all()
        )
        return [_incident_to_dict(r) for r in rows]


@app.get("/api/kb")
async def list_kb():
    return kb.list_entries()


@app.get("/api/metrics")
async def metrics():
    with get_session() as session:
        rows = session.query(Incident).all()
    total = len(rows)
    auto = sum(1 for r in rows if r.status == "auto_resolved")
    approved = sum(1 for r in rows if r.status == "approved")
    new_alerts = sum(1 for r in rows if r.status == "alerted_new")
    pending = sum(1 for r in rows if r.status == "pending_approval")
    rejected = sum(1 for r in rows if r.status == "rejected")

    # Rough "pages avoided" — every known issue would have paged a broad on-call in the old world.
    pages_avoided = auto + approved

    resolved = [r for r in rows if r.resolved_at]
    if resolved:
        avg_mttr = sum((r.resolved_at - r.created_at).total_seconds() for r in resolved) / len(resolved)
    else:
        avg_mttr = 0.0

    return {
        "total": total,
        "auto_resolved": auto,
        "approved": approved,
        "pending_approval": pending,
        "alerted_new": new_alerts,
        "rejected": rejected,
        "pages_avoided": pages_avoided,
        "avg_mttr_seconds": round(avg_mttr, 2),
    }


@app.post("/api/approve/{incident_id}")
async def approve(incident_id: int):
    with get_session() as session:
        inc = session.get(Incident, incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="incident not found")
        if inc.status != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=f"incident is in status '{inc.status}', not pending_approval",
            )
        if not inc.recommended_action:
            raise HTTPException(status_code=400, detail="no recommended action to run")
        action = inc.recommended_action

    result = await asyncio.to_thread(runner.execute, action)

    with get_session() as session:
        inc = session.get(Incident, incident_id)
        inc.action_output = result["output"]
        inc.status = "approved" if result["success"] else "failed"
        inc.resolved_at = datetime.utcnow()
        session.commit()
        session.refresh(inc)
        payload = _incident_to_dict(inc)

    await notifier.emit({"type": "approval_result", "incident": payload})
    return payload


@app.post("/api/reject/{incident_id}")
async def reject(incident_id: int):
    with get_session() as session:
        inc = session.get(Incident, incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="incident not found")
        if inc.status != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=f"incident is in status '{inc.status}', not pending_approval",
            )
        inc.status = "rejected"
        inc.resolved_at = datetime.utcnow()
        session.commit()
        session.refresh(inc)
        payload = _incident_to_dict(inc)

    await notifier.emit({"type": "approval_result", "incident": payload})
    return payload


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    q = notifier.subscribe()
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        notifier.unsubscribe(q)
