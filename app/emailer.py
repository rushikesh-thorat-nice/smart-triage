"""Async email notifications for Smart Triage via Gmail SMTP.

Two triggers:
  - alerted_new: novel incident with no KB match — manual review needed.
  - investigated: AI agent finished — sends full investigation report.

Disabled automatically when SMTP_USER / SMTP_PASSWORD are not set.
"""
from __future__ import annotations

import asyncio
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .config import settings


def _team_email(team: str) -> str | None:
    try:
        mapping: dict[str, str] = json.loads(settings.team_contacts)
    except Exception:
        mapping = {}
    return mapping.get(team) or settings.notification_email or None


def _is_configured() -> bool:
    return bool(settings.smtp_user and settings.smtp_password)


def _send_sync(to: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, [to], msg.as_string())


async def _send(to: str, subject: str, html: str) -> None:
    try:
        await asyncio.to_thread(_send_sync, to, subject, html)
        print(f"[emailer] sent to {to}: {subject}")
    except Exception as exc:
        print(f"[emailer] failed to send to {to}: {exc}")


# ── HTML helpers ──────────────────────────────────────────────────────────────

_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f172a; color: #e2e8f0; margin: 0; padding: 0; }
  .wrap { max-width: 640px; margin: 32px auto; padding: 24px;
          background: #1e293b; border-radius: 12px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { font-size: 13px; color: #94a3b8; margin-bottom: 20px; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 99px;
           font-size: 12px; font-weight: 600; }
  .badge-rose   { background: #4c0519; color: #fda4af; }
  .badge-amber  { background: #451a03; color: #fcd34d; }
  .badge-purple { background: #2e1065; color: #d8b4fe; }
  .badge-slate  { background: #334155; color: #94a3b8; border: 1px solid #475569; }
  .block { background: #0f172a; border-radius: 8px; padding: 14px 16px;
           margin: 14px 0; font-size: 13px; }
  .label { font-size: 11px; color: #64748b; text-transform: uppercase;
           letter-spacing: .05em; margin-bottom: 4px; }
  .mono { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px;
          color: #cbd5e1; word-break: break-all; }
  .footer { margin-top: 24px; font-size: 11px; color: #475569; }
  a { color: #818cf8; }
"""


def _wrap(body: str) -> str:
    return f"<html><head><style>{_STYLE}</style></head><body><div class='wrap'>{body}</div></body></html>"


def _sev_badge(sev: str) -> str:
    cls = {"critical": "badge-rose", "high": "badge-rose", "medium": "badge-amber"}.get(sev, "badge-slate")
    return f"<span class='badge {cls}'>{sev.upper()}</span>"


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Public API ────────────────────────────────────────────────────────────────

async def notify_new_incident(inc: dict[str, Any]) -> None:
    if not _is_configured():
        return
    to = _team_email(inc.get("owner_team", ""))
    if not to:
        print(f"[emailer] no contact for team '{inc.get('owner_team')}' — skipping")
        return

    product   = inc.get("product", "unknown")
    team      = inc.get("owner_team", "unknown")
    sev       = inc.get("severity", "unknown")
    log_line  = (inc.get("log_line") or "")[:400]
    reasoning = inc.get("reasoning") or ""
    inc_id    = inc.get("id", "?")

    subject = f"[Smart Triage] NEW incident #{inc_id} — {product} ({sev.upper()})"

    body = f"""
      <h1>New incident — no known resolution</h1>
      <p class='sub'>Smart Triage detected an issue it has not seen before. Manual review required.</p>

      <div style='display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;'>
        {_sev_badge(sev)}
        <span class='badge badge-slate'>{_esc(product)}</span>
        <span class='badge badge-slate'>team: {_esc(team)}</span>
        <span class='badge badge-amber'>NEW — no KB match</span>
      </div>

      <div class='block'>
        <div class='label'>Log line</div>
        <div class='mono'>{_esc(log_line)}</div>
      </div>

      {f"<div class='block'><div class='label'>AI reasoning</div><div style='font-size:13px;color:#94a3b8;'>{_esc(reasoning)}</div></div>" if reasoning else ""}

      <p style='font-size:13px;color:#94a3b8;'>
        This issue does not match any entry in the knowledge base.
        Please investigate manually — if it recurs, add it to the KB so future occurrences are handled automatically.
      </p>

      <div class='footer'>Smart Triage &nbsp;·&nbsp; incident #{inc_id} &nbsp;·&nbsp; <a href='http://localhost:8000'>Open dashboard</a></div>
    """
    await _send(to, subject, _wrap(body))


async def notify_investigation_done(inc: dict[str, Any]) -> None:
    if not _is_configured():
        return

    report: dict = inc.get("investigation_report") or {}
    if not report or "error" in report:
        return

    page_team = report.get("page_team") or inc.get("owner_team", "")
    to = _team_email(page_team)
    if not to:
        print(f"[emailer] no contact for team '{page_team}' — skipping")
        return

    product    = inc.get("product", "unknown")
    sev        = inc.get("severity", "unknown")
    inc_id     = inc.get("id", "?")
    root_cause = report.get("root_cause_hypothesis", "")
    faulty     = report.get("faulty_component", "")
    confidence = int((report.get("confidence") or 0) * 100)
    fix        = report.get("proposed_fix", "")
    summary    = report.get("summary", "")
    evidence: list[str] = report.get("evidence") or []

    subject = f"[Smart Triage] Investigation complete #{inc_id} — {product}: {faulty} ({confidence}% conf)"

    evidence_rows = "".join(
        f"<li class='mono' style='margin-bottom:4px;'>• {_esc(e)}</li>"
        for e in evidence[:8]
    )
    evidence_block = f"<ul style='margin:6px 0 0;padding:0;list-style:none;'>{evidence_rows}</ul>" if evidence_rows else ""

    body = f"""
      <h1>Investigation complete</h1>
      <p class='sub'>Smart Triage's investigator agent finished analysing incident #{inc_id}.</p>

      <div style='display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;'>
        {_sev_badge(sev)}
        <span class='badge badge-slate'>{_esc(product)}</span>
        <span class='badge badge-purple'>page: {_esc(page_team)}</span>
        <span class='badge badge-purple'>{confidence}% confidence</span>
      </div>

      <div class='block'>
        <div class='label'>Root cause</div>
        <div style='font-size:14px;font-weight:600;color:#e2e8f0;margin-bottom:4px;'>{_esc(root_cause)}</div>
        <div style='font-size:12px;color:#94a3b8;'>Faulty component: <strong style='color:#d8b4fe;'>{_esc(faulty)}</strong></div>
      </div>

      <div class='block'>
        <div class='label'>Proposed fix</div>
        <div style='font-size:13px;color:#e2e8f0;'>{_esc(fix)}</div>
      </div>

      {f"<div class='block'><div class='label'>Summary</div><div style='font-size:13px;color:#94a3b8;font-style:italic;'>{_esc(summary)}</div></div>" if summary else ""}

      {f"<div class='block'><div class='label'>Evidence ({len(evidence)} items)</div>{evidence_block}</div>" if evidence else ""}

      <div class='footer'>Smart Triage &nbsp;·&nbsp; incident #{inc_id} &nbsp;·&nbsp; <a href='http://localhost:8000'>Open dashboard</a></div>
    """
    await _send(to, subject, _wrap(body))
