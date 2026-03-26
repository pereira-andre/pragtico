"""Security utilities — CSRF protection and rate limiting."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict
from functools import wraps
from threading import Lock
from typing import Callable

from flask import abort, request, session


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------

CSRF_TOKEN_KEY = "_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"

# Endpoints whose path starts with /api/ send JSON, not form data.
# They rely on SameSite cookies and JSON Content-Type for protection.
_API_PREFIX = "/api/"


def generate_csrf_token() -> str:
    """Return the current session CSRF token, creating one if absent."""
    if CSRF_TOKEN_KEY not in session:
        session[CSRF_TOKEN_KEY] = secrets.token_hex(32)
    return session[CSRF_TOKEN_KEY]


def _validate_csrf_token() -> bool:
    """Check the request carries a valid CSRF token."""
    expected = session.get(CSRF_TOKEN_KEY)
    if not expected:
        return False
    token = request.form.get(CSRF_FIELD) or request.headers.get(CSRF_HEADER) or ""
    return secrets.compare_digest(token, expected)


def init_csrf(app):
    """Register CSRF context processor and before_request guard on *app*."""

    @app.context_processor
    def _inject_csrf():
        return {"csrf_token": generate_csrf_token}

    @app.before_request
    def _check_csrf():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        # JSON API endpoints are exempt — protected by SameSite + Content-Type
        if request.path.startswith(_API_PREFIX):
            return None
        # Logout beacon is fire-and-forget from navigator.sendBeacon
        if request.endpoint == "auth.logout_beacon":
            return None
        if not _validate_csrf_token():
            abort(403)
        return None


# ---------------------------------------------------------------------------
# In-memory rate limiter
# ---------------------------------------------------------------------------

class _RateBucket:
    __slots__ = ("timestamps",)

    def __init__(self):
        self.timestamps: list[float] = []


class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Parameters
    ----------
    max_calls : int
        Maximum number of calls allowed inside *window_seconds*.
    window_seconds : int
        Length of the sliding window in seconds.
    """

    def __init__(self, max_calls: int = 20, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._buckets: dict[str, _RateBucket] = defaultdict(_RateBucket)
        self._lock = Lock()

    def _cleanup_bucket(self, bucket: _RateBucket, now: float) -> None:
        cutoff = now - self.window_seconds
        bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[key]
            self._cleanup_bucket(bucket, now)
            if len(bucket.timestamps) >= self.max_calls:
                return False
            bucket.timestamps.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[key]
            self._cleanup_bucket(bucket, now)
            return max(0, self.max_calls - len(bucket.timestamps))


# Pre-configured limiters
login_limiter = RateLimiter(max_calls=10, window_seconds=60)
api_limiter = RateLimiter(max_calls=30, window_seconds=60)


def rate_limit(limiter: RateLimiter, key_func: Callable | None = None):
    """Decorator that rejects requests when the rate limit is exceeded."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if key_func:
                key = key_func()
            else:
                key = request.remote_addr or "unknown"
            if not limiter.is_allowed(key):
                abort(429)
            return f(*args, **kwargs)
        return wrapper
    return decorator
