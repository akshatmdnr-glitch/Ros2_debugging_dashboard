"""Publish/subscribe event fan-out for the real-time channel (Phase 11).

`DebuggerApp.refresh()` produces the authoritative event stream every cycle
(diagnostic / correlation / incident transitions). This module is a tiny,
thread-safe fan-out: subscribers register a callable (a "sink") and receive
every published message.

Design rules:
  * NO buffering and NO replay. A subscriber that joins late starts at the
    next message; the dashboard fetches a full HTTP snapshot on (re)connect
    instead of replaying history (Phase 6 history is in-memory only, and a
    client must converge to the CURRENT truth, not to a log).
  * publish() runs on the collector/demo thread. A sink may bridge into an
    event loop (asyncio) with `loop.call_soon_threadsafe(...)`.
  * A slow or broken sink must never break the observation cycle, so sink
    exceptions are swallowed. The dashboard re-syncs from a snapshot anyway.
"""

from __future__ import annotations

import threading
from typing import Callable, Set


class EventBroadcaster:
    """Thread-safe fan-out of observation-cycle messages to subscribers."""

    def __init__(self) -> None:
        self._sinks: Set[Callable[[dict], None]] = set()
        self._lock = threading.Lock()

    def subscribe(self, sink: Callable[[dict], None]) -> Callable[[], None]:
        """Register a sink; returns an unsubscribe callable."""
        with self._lock:
            self._sinks.add(sink)
        return lambda: self.unsubscribe(sink)

    def unsubscribe(self, sink: Callable[[dict], None]) -> None:
        with self._lock:
            self._sinks.discard(sink)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._sinks)

    def publish(self, message: dict) -> None:
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink(message)
            except Exception:
                # A slow/broken subscriber must not stop the observation
                # cycle; the dashboard re-syncs from a snapshot on reconnect.
                continue
