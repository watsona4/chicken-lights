#!/usr/bin/env python3
import sys
import time
from pathlib import Path


def read_int(path: str) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


def read_str(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return None


def main() -> int:
    now = int(time.time())

    phase = read_str("/tmp/phase") or "unknown"

    # In active phase, we should be ticking every minute (you write /tmp/last_tick each publish loop).
    if phase == "active":
        last_tick = read_int("/tmp/last_tick")
        if last_tick is None:
            return 1
        # Allow some slack: 3 minutes
        if now - last_tick > 180:
            return 1
        return 0

    # In sleep phase, we may intentionally not tick for hours. Use next_wake.
    if phase == "sleep":
        next_wake = read_int("/tmp/next_wake")
        if next_wake is None:
            # If we don't know when we wake, treat as unhealthy
            return 1
        # Healthy if we haven't missed the wake time by more than 10 minutes
        if now > next_wake + 600:
            return 1
        return 0

    # In idle phase, the day is done. Container is expected to just wait for tomorrow.
    if phase == "idle":
        return 0

    # Unknown phase: be conservative
    return 1


if __name__ == "__main__":
    sys.exit(main())
