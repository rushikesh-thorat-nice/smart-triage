import asyncio
from pathlib import Path
from typing import AsyncIterator

from .config import settings


async def tail_lines(path: str | None = None, poll_interval: float = 0.5) -> AsyncIterator[str]:
    """Async generator yielding new lines appended to a log file.

    Starts at end-of-file so existing lines aren't replayed. Handles the file
    being created after startup (config.py touches it, but keeps working if it
    is rotated away).
    """
    path = path or settings.log_file
    p = Path(path)

    while not p.exists():
        await asyncio.sleep(poll_interval)

    with p.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(0, 2)  # seek to end
        buffer = ""
        while True:
            chunk = fh.read()
            if not chunk:
                await asyncio.sleep(poll_interval)
                continue
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    yield line
