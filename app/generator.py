"""Simulated log emitter — appends realistic incident lines into sample_logs/app.log.

Run standalone: `python -m app.generator`

Emits three kinds of traffic:
  - Simple known issues (single log line matching a KB entry)
  - Novel issues (single log line, no KB match — exercises the alert-only path)
  - Complex multi-service bursts (a coordinated set of lines from several services
    around the same time, exercising the investigator agent path)
"""
from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path

from .config import settings


# Known-issue log lines (roughly aligned with kb_seed.json patterns).
KNOWN_LINES = [
    "ERROR payment-service - java.lang.OutOfMemoryError: Java heap space at com.acme.payment.TransactionProcessor.process (heap dump written to /tmp/hs_err.hprof)",
    "ERROR orders-api - HikariPool-1 - Connection is not available, request timed out after 30000ms (pool exhausted)",
    "WARN log-shipper - filebeat harvester: write /var/log/app.log: no space left on device (disk 100% full)",
    "FATAL auth-gateway - tls: expired certificate: x509: certificate has expired or is not yet valid for auth-gateway.example.com",
    "WARN cache-service - redis maxmemory reached, evicted_keys=1523 used_memory=95% LRU eviction active",
    "ERROR checkout-web - readiness probe failed HTTP 503; deployment rollout stuck 0/3 ready, CrashLoopBackOff",
    "ERROR inventory-service - upstream returned 502, elevated 5xx rate at 15%, p99 latency spike; upstream connect error",
    "WARN reports-cron - job running for 3600s (expected 600s), no heartbeat, marking as hung",
]

# Novel / unknown issues to exercise the alert-only path.
NOVEL_LINES = [
    "ERROR search-service - elasticsearch cluster went yellow, replica shards unassigned on node-3",
    "WARN billing-worker - stripe webhook signature validation failed for event evt_1QzX9Ab (retry queued)",
    "ERROR ml-inference - model artifact checksum mismatch for model_v42.bin; refusing to load",
    "ERROR notifications-svc - twilio API returned 429 rate_limit_exceeded on SMS burst to +1***",
]


# Complex multi-service bursts. Each entry is a scenario that emits a
# coordinated sequence of log lines from several services. The final "trigger"
# line is what the KB entry matches against — its scenario_slug then tells the
# investigator agent which context folder to fetch.
COMPLEX_SCENARIOS = {
    "api-gateway-outage": [
        "auth-service WARN upstream DB latency=1200ms (threshold=200ms)",
        "auth-service ERROR upstream DB connection timed out after 2000ms",
        "auth-service ERROR token validation failed: DB unreachable",
        "api-gateway ERROR upstream auth-service returned 503 for token verify request",
        "api-gateway ERROR JWT verify error: auth-service unavailable, returning 503 to client",
        "auth-service ERROR OOMKilled: container exceeded memory limit 512Mi",
        "auth-service INFO restarting (attempt 1/5) CrashLoopBackOff",
        "api-gateway ERROR all upstream auth-service instances unhealthy, no healthy upstream",
        "api-gateway ERROR 503 Service Unavailable - auth-service down - tenant=acme-corp",
        "api-gateway WARN elevated 5xx rate: 98% of requests returning 503",
        # Trigger line
        "operator-alert tenant=acme-corp reports API completely down ticket=INC-9201 api-gateway 5xx storm all tenants affected",
    ],
    "db-connection-storm": [
        "pgbouncer WARN pool stats: active=20/20 idle=0 waiting=12",
        "orders-api WARN HikariPool-1 active=20 idle=0 waiting=8 connectionTimeout approaching",
        "pgbouncer ERROR no more connections allowed (pool_size=20 exhausted) client=orders-api",
        "orders-api ERROR HikariPool-1 - Connection is not available, request timed out after 30000ms",
        "pgbouncer ERROR restarting: out of memory",
        "postgres FATAL: too many connections (max_connections=200 reached)",
        "orders-api ERROR HikariPool-1 - Connection is not available, request timed out after 30000ms (pool exhausted)",
        "postgres FATAL: remaining connection slots reserved for non-replication superuser connections",
        "payments-service ERROR SQLTransientConnectionException: unable to acquire connection from pool",
        "auth-service ERROR DB connection failed: too many connections for role auth_user",
        # Trigger line
        "operator-alert customer=globex-inc reports orders failing with 500 errors ticket=INC-9205 database connection storm pgbouncer down",
    ],
    "call-recording-stopped": [
        "media-server INFO peer=recorder-3 packets_fwd=8421 rtcp_ok",
        "call-recorder INFO session=sess_abc123 asr_worker=w4 frame_count=1204",
        "file-server WARN disk_usage=/var/nfs/recordings 95% (allocated 9.5T / 10T)",
        "call-recorder ERROR TranscriptWriter write error: /mnt/recordings/sess_abc123.txt - No space left on device",
        "file-server ERROR nfsd: write failed (xid=0x8b3f) : No space left on device /var/nfs/recordings",
        "call-recorder ERROR session=sess_abc123 dropping recording, IO error",
        "call-recorder WARN transcription queue depth=4123 (soft-limit 4000)",
        "media-server WARN peer=recorder-3 forwarding stalled buffer=812",
        "file-server CRIT disk_usage=/var/nfs/recordings 100% (allocated 10T / 10T)",
        "media-server ERROR peer=recorder-3 unreachable, dropping stream after 3 retries",
        # This last line is the trigger — call-recording pipeline failed, needs investigation.
        "operator-alert customer=acme-corp reports call recording suddenly stopped ticket=INC-9142 call-recorder pipeline degraded",
    ],
}


def _emit_line(fh, line: str) -> None:
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    fh.write(f"{ts} {line}\n")
    fh.flush()


def main():
    path = Path(settings.log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    print(f"[generator] appending to {path}. Ctrl+C to stop.")

    with path.open("a", encoding="utf-8") as fh:
        # ---------- Startup burst ----------
        # Seed 10 incidents up front so the dashboard is populated the moment
        # the user opens it. Mix of simple known / simple novel / one complex
        # scenario — enough variety to show every code path (auto-resolved,
        # pending approval, alerted new, investigate).
        print("[generator] emitting startup burst of 10 incidents")
        startup_plan = [
            ("known", KNOWN_LINES[0]),        # payment-service OOM
            ("known", KNOWN_LINES[2]),        # log-shipper disk full (auto-resolve)
            ("known", KNOWN_LINES[1]),        # orders-api HikariPool
            ("novel", NOVEL_LINES[0]),        # elasticsearch yellow
            ("known", KNOWN_LINES[4]),        # cache-service redis eviction
            ("known", KNOWN_LINES[3]),        # auth-gateway TLS
            ("complex", "call-recording-stopped"),   # multi-service: storage
            ("known", KNOWN_LINES[7]),        # reports-cron hung
            ("novel", NOVEL_LINES[2]),        # ml-inference checksum mismatch
            ("complex", "api-gateway-outage"),       # multi-service: auth + gateway
            ("known", KNOWN_LINES[6]),        # inventory-service 502s
            ("complex", "db-connection-storm"),      # multi-service: DB pool
        ]
        for kind, payload in startup_plan:
            if kind == "complex":
                lines = COMPLEX_SCENARIOS[payload]
                print(f"[generator] startup: complex scenario {payload}")
                for i, line in enumerate(lines):
                    _emit_line(fh, line)
                    if i < len(lines) - 1:
                        time.sleep(0.25)
            else:
                _emit_line(fh, payload)
                print(f"[generator] startup: {payload[:100]}")
            # Small gap between incidents so the triage engine can keep up.
            time.sleep(2.5)

        # ---------- Steady state ----------
        # After the startup burst, trickle in one incident per ~minute so the
        # feed keeps updating without overwhelming the operator.
        print("[generator] switching to steady-state cadence (1 incident/min)")
        iteration = 0
        while True:
            iteration += 1

            # Every 5th steady-state incident is a complex multi-service burst.
            if iteration % 5 == 0:
                scenario_name, lines = random.choice(list(COMPLEX_SCENARIOS.items()))
                print(f"[generator] steady: complex scenario {scenario_name}")
                for i, line in enumerate(lines):
                    _emit_line(fh, line)
                    if i < len(lines) - 1:
                        time.sleep(0.3)
            else:
                # 80% known, 20% novel.
                if random.random() < 0.8:
                    line = random.choice(KNOWN_LINES)
                else:
                    line = random.choice(NOVEL_LINES)
                _emit_line(fh, line)
                print(f"[generator] steady: {line[:100]}")

            # ~1 minute between incidents.
            time.sleep(random.uniform(55, 70))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[generator] stopped.")
