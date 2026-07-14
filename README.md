# Smart Triage — AI-Powered Incident Triage

Continuously scans application logs, matches issues against a knowledge base of past incidents, and either auto-executes known remediations (with optional approval) or pages only the responsible product team when the issue is genuinely new.

**Cost benefits demonstrated:**
- Reduced paging — only the affected product team is notified
- Automated resolution of recurring issues
- Faster MTTR by leveraging historical fix knowledge
- Human-in-the-loop approval for controlled automation

## Quickstart

```bash
python -m venv .venv
source .venv/Scripts/activate    # Windows Git Bash / macOS-linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then either:
#   - LLM_PROVIDER=bedrock (default) — uses your AWS credentials from ~/.aws
#     make sure the model is enabled in your Bedrock console
#   - LLM_PROVIDER=anthropic — set ANTHROPIC_API_KEY

python scripts/seed_kb.py        # loads seed KB into Chroma + SQLite
uvicorn app.main:app --reload    # dashboard at http://localhost:8000
```

In another terminal, start the simulated log generator:

```bash
python -m app.generator
```

Open http://localhost:8000 to watch incidents flow in.

## How it works

1. **Ingest** — tails `sample_logs/app.log` line by line.
2. **Match** — embeds the log line and searches Chroma for the closest known-issue pattern.
3. **Triage** — Claude Sonnet 4.6 confirms the match, extracts the affected product, and picks a resolution.
4. **Act** — three paths, depending on the KB entry:
   - Simple + `auto_execute=true` → runs the allowlisted shell command automatically.
   - Simple + `auto_execute=false` → creates a pending approval; click Approve to run.
   - Complex (`action_type=investigate`) → click **Investigate** and the investigator agent runs (below).
5. **Alert** — for unmatched issues, notifies only the product team responsible.

## Complex-issue investigation

For multi-component incidents (e.g. `call-recording-stopped` — spans media-server, call-recorder, file-server), a single "restart X" command isn't the right response. The KB entry sets `action_type: investigate` and points at a **scenario slug** in a companion knowledge repo: [rushikesh-thorat-nice/demo](https://github.com/rushikesh-thorat-nice/demo).

When you click **Investigate**, the agent:

1. Fetches the scenario folder from the repo:
   - `incident.yaml` (hypotheses, components, escalation)
   - `runbook.md` (how a human investigates)
   - `components/<name>.md` (per-service log patterns)
   - `code-references.md` (relevant repo paths)
2. Grabs the last ~40 log lines from the app so Claude has time-window context.
3. Calls Claude Sonnet 4.6 with structured output — it returns a JSON verdict:
   - `root_cause_hypothesis` (one of the yaml's h1–h5)
   - `faulty_component`
   - `evidence` (quoted log lines)
   - `proposed_fix`
   - `page_team` (may override the initial team based on the diagnosis)

Every step streams live to the **Investigator terminal** panel in the dashboard — you see the GitHub fetches, prompt assembly, Claude's verdict, and the evidence appear line-by-line.

Adding a new complex scenario:
- Create `incidents/<slug>/` in [rushikesh-thorat-nice/demo](https://github.com/rushikesh-thorat-nice/demo) following the structure documented there.
- Add a KB entry in `data/kb_seed.json` with `"action_type": "investigate"` and `"scenario_slug": "<slug>"`.
- Re-seed: `python -c "from app import kb; kb.seed_from_file()"`.

## Architecture

- `app/ingest.py` — log tailer
- `app/kb.py` — Chroma vector store + seed loader
- `app/triage.py` — Claude Sonnet 4.6 decision engine (structured outputs)
- `app/runner.py` — allowlisted subprocess executor
- `app/investigator.py` — investigator agent for complex multi-component incidents
- `app/notifier.py` — console + WebSocket push (main feed + investigator terminal)
- `app/main.py` — FastAPI + dashboard + background tasks
- `app/static/index.html` — single-file dashboard with live feed + investigator terminal

## Demo tips

The seed KB uses harmless commands (`echo`, `date`) so nothing touches real infra. To test the unknown-issue path, append a novel error line to `sample_logs/app.log` that doesn't match any pattern.
