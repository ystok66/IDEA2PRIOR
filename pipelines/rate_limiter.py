"""
Thread-safe sliding-window rate limiter.

This module provides per-request rate limiting for external API calls.
All throttling must be per-request via wait_for_slot(). Do not group
tasks into batches and sleep between batches.

Usage:
    from pipelines.rate_limiter import openalex_rate_limiter
    
    def call_openalex_api(...):
        openalex_rate_limiter.wait_for_slot()  # Wait for slot
        response = requests.get(...)  # Then make request
"""

import time
import threading
from collections import deque
from typing import Deque


class SimpleRateLimiter:
    """
    Thread-safe sliding-window rate limiter.

    Guarantees at most `max_calls` within any `period` seconds,
    across all threads sharing this limiter.
    
    This is a sliding-window implementation, NOT a batch + sleep pattern.
    Each call to wait_for_slot() independently computes its own wait time.
    """

    def __init__(self, max_calls: int, period: float, *, clock=time.monotonic, sleep=time.sleep) -> None:
        """
        Initialize the rate limiter.
        
        Args:
            max_calls: Maximum number of calls allowed within the period
            period: Time window in seconds
        """
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls <= 0 or period <= 0:
            raise ValueError("max_calls and period must be positive")
        self.max_calls = max_calls
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._timestamps: Deque[float] = deque()

    def wait_for_slot(self, *, max_wait_seconds=None) -> None:
        """
        Block the current thread until a new request is allowed.

        This method:
        1. Cleans up old timestamps outside the current window.
        2. If at capacity, computes exact sleep time needed.
        3. Sleeps outside the lock, then records the new timestamp.
        
        Thread-safe: multiple threads can call this concurrently.
        """
        started = self._clock()
        while True:
            with self._lock:
                now = self._clock()
                # Drop timestamps older than `period`
                while self._timestamps and (now - self._timestamps[0]) >= self.period:
                    self._timestamps.popleft()

                if len(self._timestamps) < self.max_calls:
                    # We have capacity, record and return
                    self._timestamps.append(now)
                    return

                # At capacity: compute minimal sleep time until oldest expires
                oldest = self._timestamps[0]
                sleep_for = self.period - (now - oldest) + 0.001  # Small buffer
                if max_wait_seconds is not None:
                    remaining = max_wait_seconds - (now - started)
                    if sleep_for > remaining:
                        raise TimeoutError("Provider pacing exceeds the remaining request budget")

            # Sleep outside the lock to avoid blocking other threads
            if sleep_for > 0:
                self._sleep(sleep_for)
            # Loop will re-check and append once a slot is free


# =============================================================================
# GLOBAL RATE LIMITER INSTANCES
# =============================================================================
# These are module-level singletons shared by all threads.
# All HTTP requests to these services MUST go through these limiters.

# Legacy local pacing settings, not verified account/service quotas.
openalex_rate_limiter = SimpleRateLimiter(max_calls=8, period=1.0)

# Semantic Scholar legacy local pacing.
s2_rate_limiter = SimpleRateLimiter(max_calls=1, period=2.0)

# Legacy LLM pacing, not proof of a provider quota. New request admission is
# coordinated by pipelines.runtime.RequestScheduler with explicit RPM/TPM.
llm_rate_limiter = SimpleRateLimiter(max_calls=20, period=1.0)

# PDF downloads: 5/s to avoid hammering arbitrary PDF hosts
pdf_rate_limiter = SimpleRateLimiter(max_calls=5, period=1.0)
