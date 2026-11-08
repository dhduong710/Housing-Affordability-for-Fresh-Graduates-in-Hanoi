"""Polite pacing and server-directed cooldowns for sequential page requests."""

import math
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_after_seconds(value, now=None):
    """Accept HTTP delay-seconds or HTTP-date; malformed headers use backoff."""
    now = now or datetime.now(timezone.utc)
    value = str(value or '').strip()
    if value.isascii() and value.isdigit():
        try:
            seconds = float(value)
            return seconds if math.isfinite(seconds) else None
        except (ValueError, OverflowError):
            return None
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return max(0.0, (deadline - now).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


class RequestPacer:
    def __init__(self, delay, jitter=5, backoff=15):
        self.delay, self.jitter, self.backoff = delay, jitter, backoff
        self.last_finished = None

    def wait(self):
        if self.last_finished is None:
            return 0.0
        interval = self.delay + random.uniform(0, self.jitter)
        seconds = max(0.0, self.last_finished + interval - time.monotonic())
        self.sleep(seconds)
        return seconds

    def finished(self):
        self.last_finished = time.monotonic()

    @staticmethod
    def sleep(seconds):
        # Short slices allow Ctrl+C and avoid one long uninterruptible sleep.
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            time.sleep(min(1.0, remaining))

    def retry_delay(self, attempt, header=None, now=None):
        requested = retry_after_seconds(header, now)
        return max(self.delay, self.backoff * 2 ** (attempt - 1), requested or 0)

    def slow_down(self):
        self.delay = min(max(self.delay, 120), self.delay * 2)
