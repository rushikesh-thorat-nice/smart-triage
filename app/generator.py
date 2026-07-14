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
        iteration = 0
        while True:
            iteration += 1
            # Every ~4th iteration, emit a complex multi-service burst so the
            # investigator path gets exercised during the demo.
            if iteration % 4 == 0:
                scenario_name, lines = random.choice(list(COMPLEX_SCENARIOS.items()))
                print(f"[generator] emitting complex scenario: {scenario_name}")
                for i, line in enumerate(lines):
                    _emit_line(fh, line)
                    # Small stagger so lines land in order, but the whole
                    # burst arrives together as one incident cluster.
                    if i < len(lines) - 1:
                        time.sleep(0.3)
                # Long pause AFTER a complex burst — gives the viewer time to
                # read the pending card and click Investigate without more
                # incidents piling up.
                time.sleep(random.uniform(75, 100))
                continue

            # Otherwise: 80% known simple, 20% novel.
            if random.random() < 0.8:
                line = random.choice(KNOWN_LINES)
            else:
                line = random.choice(NOVEL_LINES)
            _emit_line(fh, line)
            print(f"[generator] emitted: {line[:120]}")
            # Slower cadence: one incident every 60-90 seconds.
            time.sleep(random.uniform(60, 90))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[generator] stopped.")
