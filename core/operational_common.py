"""Shared operational lookup helpers."""

import re
import unicodedata

from core import services
from core.access_control import filter_port_activity_for_session
from domain.chat_actions import visible_port_calls_from_activity


def _operational_lookup_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def current_visible_port_calls(window_days: int = 120) -> list[dict]:
    """Return port calls visible to the current session filtered by the given window."""
    port_activity = services.store.get_port_activity_snapshot(window_days=window_days)
    port_activity = filter_port_activity_for_session(port_activity)
    return visible_port_calls_from_activity(port_activity)


def current_resolvable_port_calls() -> list[dict]:
    """Return all port calls visible to the session over a 10-year window for action resolution."""
    return current_visible_port_calls(window_days=3650)
