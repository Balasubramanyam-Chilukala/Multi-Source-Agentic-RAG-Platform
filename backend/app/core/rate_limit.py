"""In-process sliding-window rate limiter.

Adequate for the single-instance free-tier deployment this project targets. With several
replicas each instance enforces its own window; a shared store (e.g. Redis or a Postgres table)
would be needed for a global limit. This limitation is documented in docs/security.md.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.errors import AppError


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class SlidingWindowLimiter:
    clock: Callable[[], float] = time.monotonic
    _hits: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def check(self, key: str, *, limit: int, window_seconds: float) -> None:
        now = self.clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window_seconds - (now - hits[0])) + 1)
            raise RateLimitedError("Too many requests; please slow down.", retry_after=retry_after)
        hits.append(now)
        if len(self._hits) > 50_000:  # bound memory under key churn
            self._evict(now, window_seconds)

    def _evict(self, now: float, window_seconds: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= window_seconds]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()
