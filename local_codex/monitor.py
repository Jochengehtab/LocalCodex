"""Lifecycle coordination for local Codex launchers and the Windows monitor."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class Lease:
    lease_id: str
    process_id: int
    created_at: float
    updated_at: float


class LeaseRegistry:
    """Small in-memory heartbeat registry for concurrently running launchers."""

    def __init__(self, ttl_seconds: float = 15.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._leases: dict[str, Lease] = {}
        self.ever_had_lease = False
        self.empty_since: float | None = None

    def register(self, lease_id: str, process_id: int, now: float | None = None) -> Lease:
        moment = time.time() if now is None else now
        with self._lock:
            lease = Lease(lease_id, process_id, moment, moment)
            self._leases[lease_id] = lease
            self.ever_had_lease = True
            self.empty_since = None
            return lease

    def heartbeat(self, lease_id: str, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                return False
            lease.updated_at = moment
            return True

    def release(self, lease_id: str, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        with self._lock:
            removed = self._leases.pop(lease_id, None) is not None
            if not self._leases and self.ever_had_lease:
                self.empty_since = self.empty_since or moment
            return removed

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        moment = time.time() if now is None else now
        with self._lock:
            stale = [
                lease_id
                for lease_id, lease in self._leases.items()
                if moment - lease.updated_at > self.ttl_seconds
            ]
            for lease_id in stale:
                self._leases.pop(lease_id, None)
            if not self._leases and self.ever_had_lease:
                self.empty_since = self.empty_since or moment
            elif self._leases:
                self.empty_since = None
            return {
                "active_count": len(self._leases),
                "stale_removed": len(stale),
                "ever_had_lease": self.ever_had_lease,
                "empty_seconds": (
                    max(moment - self.empty_since, 0.0)
                    if self.empty_since is not None
                    else 0.0
                ),
            }
