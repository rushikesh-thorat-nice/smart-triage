import asyncio
import json
import subprocess
import sys
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import emailer, investigator, kb, runner, triage
from .config import settings
from .db import Incident, get_session, init_db
from .ingest import tail_lines
from .notifier import notifier


STATIC_DIR = Path(__file__).parent / "static"

# Rolling buffer of recent log lines so the investigator can send a small
# time-window of context along with the trigger line.
_recent_log_lines: deque[str] = deque(maxlen=40)


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
        "resolution_steps": json.loads(inc.resolution_steps) if inc.resolution_steps else None,
        "action_type": inc.action_type,
        "scenario_slug": inc.scenario_slug,
        "status": inc.status,
        "action_output": inc.action_output,
        "investigation_report": json.loads(inc.investigation_report) if inc.investigation_report else None,
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
    }


async def _handle_log_line(line: str) -> None:
    _recent_log_lines.append(line)

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
    resolution_steps = matched_kb.resolution_steps if matched_kb else None
    action_type = matched_kb.action_type if matched_kb else "execute"
    scenario_slug = matched_kb.scenario_slug if matched_kb else None

    if matched_kb and matched_kb.auto_execute and action_type == "execute":
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
        recommended_action=matched_kb.resolution_summary if matched_kb else None,
        resolution_steps=resolution_steps,
        action_type=action_type,
        scenario_slug=scenario_slug,
        status=status,
    )

    with get_session() as session:
        session.add(inc)
        session.commit()
        session.refresh(inc)
        incident_id = inc.id

    if status == "auto_resolved" and resolution_steps:
        steps_list = json.loads(resolution_steps)
        outputs = await _run_steps(incident_id, steps_list)
        with get_session() as session:
            row = session.get(Incident, incident_id)
            if row:
                row.action_output = "\n".join(outputs)
                row.status = "auto_resolved"
                row.resolved_at = datetime.utcnow()
                session.commit()
                session.refresh(row)
                inc = row

    await notifier.emit({"type": "incident", "incident": _incident_to_dict(inc)})

    if status == "alerted_new":
        await emailer.notify_new_incident(_incident_to_dict(inc))


async def _run_steps(incident_id: int, steps: list[str]) -> list[str]:
    """Execute each echo step in sequence, streaming progress to the terminal."""
    outputs: list[str] = []
    for step in steps:
        await notifier.emit_term(incident_id, "step", step.replace("echo ", "", 1))
        result = await asyncio.to_thread(runner.execute, step)
        line = result["output"]
        outputs.append(line)
        await notifier.emit_term(incident_id, "info", line)
    return outputs


async def _run_investigation(incident_id: int, scenario_slug: str, log_line: str) -> dict:
    context_snapshot = list(_recent_log_lines)

    async def term(level: str, text: str) -> None:
        await notifier.emit_term(incident_id, level, text)

    report = await investigator.investigate(
        incident_id=incident_id,
        scenario_slug=scenario_slug,
        log_line=log_line,
        recent_context_lines=context_snapshot,
        term=term,
    )
    return report


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
    investigated = sum(1 for r in rows if r.status == "investigated")
    investigating = sum(1 for r in rows if r.status == "investigating")
    new_alerts = sum(1 for r in rows if r.status == "alerted_new")
    pending = sum(1 for r in rows if r.status == "pending_approval")
    rejected = sum(1 for r in rows if r.status == "rejected")

    # Rough "pages avoided" — every known issue would have paged a broad on-call
    # in the old world. Investigation successes count as pages narrowed, since
    # they route to the correct team instead of every team.
    pages_avoided = auto + approved + investigated

    resolved = [r for r in rows if r.resolved_at]
    if resolved:
        avg_mttr = sum((r.resolved_at - r.created_at).total_seconds() for r in resolved) / len(resolved)
    else:
        avg_mttr = 0.0

    return {
        "total": total,
        "auto_resolved": auto,
        "approved": approved,
        "investigated": investigated,
        "investigating": investigating,
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
        action_type = inc.action_type
        scenario_slug = inc.scenario_slug
        log_line = inc.log_line
        resolution_steps = json.loads(inc.resolution_steps) if inc.resolution_steps else None

    # Investigate-type approvals launch the agent instead of running a shell command.
    if action_type == "investigate":
        if not scenario_slug:
            raise HTTPException(status_code=400, detail="investigate action with no scenario_slug")

        with get_session() as session:
            row = session.get(Incident, incident_id)
            row.status = "investigating"
            session.commit()
            session.refresh(row)
            payload = _incident_to_dict(row)
        await notifier.emit({"type": "incident", "incident": payload})

        report = await _run_investigation(incident_id, scenario_slug, log_line)

        with get_session() as session:
            row = session.get(Incident, incident_id)
            row.investigation_report = json.dumps(report)
            row.status = "failed" if "error" in report else "investigated"
            row.resolved_at = datetime.utcnow()
            # Overwrite owner_team from the investigation if the model chose a
            # more specific team.
            if isinstance(report.get("page_team"), str) and report["page_team"]:
                row.owner_team = report["page_team"]
            session.commit()
            session.refresh(row)
            payload = _incident_to_dict(row)

        await notifier.emit({"type": "approval_result", "incident": payload})
        await emailer.notify_investigation_done(payload)
        return payload

    # Default: run the echo steps in sequence.
    if not resolution_steps:
        raise HTTPException(status_code=400, detail="no resolution steps to run")

    outputs = await _run_steps(incident_id, resolution_steps)

    with get_session() as session:
        inc = session.get(Incident, incident_id)
        inc.action_output = "\n".join(outputs)
        inc.status = "approved"
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


@app.websocket("/ws-term")
async def websocket_terminal(ws: WebSocket):
    """Live stream of the investigator agent's step-by-step trace."""
    await ws.accept()
    q = notifier.subscribe_term()
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        notifier.unsubscribe_term(q)
