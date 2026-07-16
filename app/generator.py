"""Simulated log emitter — appends realistic incident lines into sample_logs/app.log.

Run standalone: `python -m app.generator`

Emits three kinds of traffic matching the 3 KB scenarios:
  - Scenario 1: File server disk full (auto-resolve, no approval)
  - Scenario 2: TLS certs expired (approval required)
  - Scenario 3: Call recording stopped mid-way (complex, multi-component investigation)
"""
from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path

from .config import settings


# Scenario 1: File server disk full — auto-resolvable, no approval required
DISK_FULL_LINES = [
    "ERROR file-server - write /var/nfs/recordings: no space left on device (disk 100% full)",
    "WARN file-server - disk_usage=/var/nfs/recordings 100% (allocated 10T / 10T) - writes failing",
    "ERROR file-server - critical: partition /data at 100% capacity, all writes rejected",
]

# Scenario 2: TLS certs expired — automatable but approval required
TLS_EXPIRED_LINES = [
    "FATAL auth-gateway - tls: expired certificate: x509: certificate has expired for auth-gateway.example.com",
    "ERROR auth-gateway - TLS handshake failed: NET::ERR_CERT_DATE_INVALID for client 10.0.1.5",
    "ERROR auth-gateway - x509: certificate has expired or is not yet valid ssl cert expired",
]

# Scenario 3: Call recording stopped mid-way — complex multi-component investigation
CALL_RECORDING_SCENARIO = [
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
    # Trigger line — this is what the KB matches against
    "operator-alert customer=acme-corp reports call recording suddenly stopped ticket=INC-9142 call-recorder pipeline degraded",
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
        # ---------- Startup burst ----------
        # Emit one of each scenario so the dashboard is populated immediately.
        print("[generator] emitting startup burst — one of each scenario")

        # Scenario 1: disk full (auto-resolve)
        line = DISK_FULL_LINES[0]
        _emit_line(fh, line)
        print(f"[generator] startup: scenario-1 disk-full: {line[:100]}")
        time.sleep(2.5)

        # Scenario 2: TLS expired (pending approval)
        line = TLS_EXPIRED_LINES[0]
        _emit_line(fh, line)
        print(f"[generator] startup: scenario-2 tls-expired: {line[:100]}")
        time.sleep(2.5)

        # Scenario 3: call recording stopped (complex investigation)
        print("[generator] startup: scenario-3 call-recording-stopped (multi-line burst)")
        for i, burst_line in enumerate(CALL_RECORDING_SCENARIO):
            _emit_line(fh, burst_line)
            if i < len(CALL_RECORDING_SCENARIO) - 1:
                time.sleep(0.25)
        time.sleep(2.5)

        # ---------- Steady state ----------
        # Trickle in one incident per ~minute cycling through all 3 scenarios.
        print("[generator] switching to steady-state cadence (1 incident/min)")
        iteration = 0
        while True:
            iteration += 1
            bucket = iteration % 3

            if bucket == 0:
                # Scenario 3: complex multi-line burst
                print("[generator] steady: scenario-3 call-recording-stopped")
                for i, burst_line in enumerate(CALL_RECORDING_SCENARIO):
                    _emit_line(fh, burst_line)
                    if i < len(CALL_RECORDING_SCENARIO) - 1:
                        time.sleep(0.3)
            elif bucket == 1:
                # Scenario 1: disk full
                line = random.choice(DISK_FULL_LINES)
                _emit_line(fh, line)
                print(f"[generator] steady: scenario-1 disk-full: {line[:100]}")
            else:
                # Scenario 2: TLS expired
                line = random.choice(TLS_EXPIRED_LINES)
                _emit_line(fh, line)
                print(f"[generator] steady: scenario-2 tls-expired: {line[:100]}")

            time.sleep(random.uniform(55, 70))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[generator] stopped.")
