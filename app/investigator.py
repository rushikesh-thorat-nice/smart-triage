"""Investigator agent: fetches structured incident context from the demo repo,
reasons over it with Claude, and streams a step-by-step trace to the dashboard
terminal.

Public entry point: `await investigate(incident_id, scenario_slug, log_line,
recent_context_lines, term_stream)` — where `term_stream` is a callable that
accepts `(level, text)` and pushes it out to the WebSocket terminal panel.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx

from .config import settings
from .triage import _make_client, _model_id


TermStream = Callable[[str, str], Awaitable[None]]


REPO_OWNER = "rushikesh-thorat-nice"
REPO_NAME = "demo"
REPO_BRANCH = "main"


def _raw_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{REPO_BRANCH}/{path}"


async def _fetch(client: httpx.AsyncClient, path: str, term: TermStream) -> str | None:
    url = _raw_url(path)
    await term("fetch", f"GET {url}")
    try:
        resp = await client.get(url, timeout=10.0)
    except httpx.HTTPError as e:
        await term("error", f"network error fetching {path}: {e}")
        return None
    if resp.status_code != 200:
        await term("warn", f"  → {resp.status_code} (skipping)")
        return None
    body = resp.text
    await term("fetch", f"  → 200 OK ({len(body)} bytes)")
    return body


INVESTIGATION_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause_hypothesis": {
            "type": "string",
            "description": "Which of the hypotheses in incident.yaml best explains the observed logs. Reference the hypothesis id (e.g. 'h3-file-server-corrupt-full').",
        },
        "faulty_component": {
            "type": "string",
            "description": "Name of the component most likely at fault (e.g. 'file-server', 'call-recorder').",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence that this is the correct root cause, 0.0 to 1.0.",
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Bullet list of specific log lines / patterns that support the diagnosis. Quote the log lines.",
        },
        "proposed_fix": {
            "type": "string",
            "description": "Concrete recommended action to remediate. Reference commands or runbook sections where possible.",
        },
        "page_team": {
            "type": "string",
            "description": "Which team to page based on incident.yaml escalation rules and the faulty component.",
        },
        "summary": {
            "type": "string",
            "description": "One-paragraph human-readable summary of the finding, suitable for the operator's dashboard.",
        },
    },
    "required": [
        "root_cause_hypothesis",
        "faulty_component",
        "confidence",
        "evidence",
        "proposed_fix",
        "page_team",
        "summary",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """You are an SRE investigator agent for a production monitoring system.

You will receive:
1. A triggering incident log line that suggests a complex multi-component issue.
2. Recent surrounding log lines from the same time window.
3. Structured incident context from a knowledge repo:
   - incident.yaml (hypotheses, components, escalation)
   - runbook.md (how a human investigates)
   - per-component notes (log patterns to look for)
   - code-references (which files to check)

Your job:
- Correlate the log lines against the hypotheses in the incident.yaml.
- Identify which single component is most likely at fault.
- Cite specific log lines as evidence.
- Propose a concrete fix action.
- Say which team to page based on the escalation section.

Be decisive but honest — if the evidence is weak, say so in `confidence` and `summary`. Do NOT invent log lines that weren't provided."""


async def investigate(
    incident_id: int,
    scenario_slug: str,
    log_line: str,
    recent_context_lines: list[str],
    term: TermStream,
) -> dict[str, Any]:
    """Run the investigation and return the structured report.

    All progress is streamed through `term`. Returns the parsed JSON report dict.
    """
    await term("info", f"─── investigation started for incident #{incident_id} ───")
    await term("info", f"scenario: {scenario_slug}")
    await term("info", f"trigger:  {log_line[:140]}")

    # Step 1: fetch the incident-context folder from the demo repo.
    await term("step", "step 1/3: fetching incident context from GitHub")

    base = f"incidents/{scenario_slug}"
    fetched: dict[str, str] = {}
    async with httpx.AsyncClient() as client:
        # Main files
        for name, path in [
            ("incident_yaml", f"{base}/incident.yaml"),
            ("runbook", f"{base}/runbook.md"),
            ("code_refs", f"{base}/code-references.md"),
        ]:
            body = await _fetch(client, path, term)
            if body:
                fetched[name] = body

        # Component notes — try common names, silently skip missing.
        components_dir = f"{base}/components"
        for comp in ("media-server", "call-recorder", "file-server"):
            body = await _fetch(client, f"{components_dir}/{comp}.md", term)
            if body:
                fetched[f"component_{comp}"] = body

    if not fetched:
        await term("error", "no context files retrieved — aborting")
        return {"error": "no context retrieved from repo"}

    await term("info", f"loaded {len(fetched)} context files, {sum(len(v) for v in fetched.values())} bytes total")

    # Step 2: assemble the prompt.
    await term("step", "step 2/3: correlating log evidence with runbook hypotheses")

    context_blob = "\n\n".join(f"=== {k} ===\n{v}" for k, v in fetched.items())
    recent_block = "\n".join(recent_context_lines) if recent_context_lines else "(no additional context lines captured)"

    user_msg = f"""TRIGGER LOG LINE:
{log_line}

RECENT LOG LINES FROM AROUND THE SAME TIME (in order):
{recent_block}

INCIDENT KNOWLEDGE (from repo):
{context_blob}

Produce the investigation report as JSON matching the schema."""

    await term("info", f"prompt assembled: {len(user_msg)} chars, {len(recent_context_lines)} context log lines")

    # Step 3: call Claude with structured output.
    await term("step", "step 3/3: asking Claude to diagnose")
    await term("claude", f"model: {_model_id()}")

    def _call_claude() -> str:
        client = _make_client()
        response = client.messages.create(
            model=_model_id(),
            max_tokens=1500,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": INVESTIGATION_SCHEMA,
                }
            },
            messages=[{"role": "user", "content": user_msg}],
        )
        return next((b.text for b in response.content if b.type == "text"), "")

    try:
        text = await asyncio.to_thread(_call_claude)
    except Exception as e:  # noqa: BLE001
        await term("error", f"Claude call failed: {e}")
        return {"error": str(e)}

    try:
        report = json.loads(text)
    except json.JSONDecodeError as e:
        await term("error", f"failed to parse response as JSON: {e}")
        return {"error": "invalid JSON from model", "raw": text}

    await term("verdict", f"root cause: {report.get('root_cause_hypothesis')} — {report.get('faulty_component')}")
    await term("verdict", f"confidence: {report.get('confidence')}")
    await term("verdict", f"paging: {report.get('page_team')}")
    await term("verdict", f"fix: {report.get('proposed_fix')}")
    for ev in report.get("evidence", []):
        await term("evidence", f"• {ev}")
    await term("info", "─── investigation complete ───")

    return report
