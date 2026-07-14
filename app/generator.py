"""Simulated log emitter — appends realistic incident lines into sample_logs/app.log.

Run standalone: `python -m app.generator`
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
        while True:
            # 80% known, 20% novel — biased so the demo hits the auto-resolve path often.
            if random.random() < 0.8:
                line = random.choice(KNOWN_LINES)
            else:
                line = random.choice(NOVEL_LINES)
            _emit_line(fh, line)
            print(f"[generator] emitted: {line[:120]}")
            time.sleep(random.uniform(5, 12))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[generator] stopped.")
