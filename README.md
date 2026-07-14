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
4. **Act** — if `auto_execute` is true, runs the allowlisted shell command. Otherwise creates a pending approval visible in the dashboard.
5. **Alert** — for unmatched issues, notifies only the product team responsible.

## Architecture

- `app/ingest.py` — log tailer
- `app/kb.py` — Chroma vector store + seed loader
- `app/triage.py` — Claude Sonnet 4.6 decision engine (structured outputs)
- `app/runner.py` — allowlisted subprocess executor
- `app/notifier.py` — console + WebSocket push
- `app/main.py` — FastAPI + dashboard + background tasks
- `app/static/index.html` — single-file dashboard

## Demo tips

The seed KB uses harmless commands (`echo`, `date`) so nothing touches real infra. To test the unknown-issue path, append a novel error line to `sample_logs/app.log` that doesn't match any pattern.
