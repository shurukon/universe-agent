"""concurrency/supervisor.py — MCP server process supervision (§6, §14).

A server that crashes 3 times in 5 minutes is paused and the user is notified,
rather than letting it crash-loop silently. Also enforces the
4-concurrent-Moons-per-Planet backpressure limit (§14).
"""
from __future__ import annotations

import time


class Supervisor:
    """Supervises MCP server subprocesses and enforces backpressure."""

    def __init__(self) -> None:
        self._crash_log: dict[str, list[float]] = {}
        self._paused: set[str] = set()
        self._moon_counts: dict[str, int] = {}  # planet_id -> active moons
        self._max_moons = 4  # §14 backpressure

    def record_crash(self, server_name: str) -> bool:
        """Record a crash. Returns True if the server should be paused."""
        now = time.time()
        log = self._crash_log.setdefault(server_name, [])
        log.append(now)
        # keep only crashes within the last 5 min
        self._crash_log[server_name] = [t for t in log if now - t < 300]
        if len(self._crash_log[server_name]) >= 3:
            self._paused.add(server_name)
            return True
        return False

    def is_paused(self, server_name: str) -> bool:
        return server_name in self._paused

    def resume(self, server_name: str) -> None:
        self._paused.discard(server_name)
        self._crash_log.pop(server_name, None)

    # ---- Moon backpressure (§14) -----------------------------------------
    def can_spawn_moon(self, planet_id: str) -> bool:
        return self._moon_counts.get(planet_id, 0) < self._max_moons

    def moon_started(self, planet_id: str) -> None:
        self._moon_counts[planet_id] = self._moon_counts.get(planet_id, 0) + 1

    def moon_finished(self, planet_id: str) -> None:
        c = self._moon_counts.get(planet_id, 0)
        if c > 0:
            self._moon_counts[planet_id] = c - 1

    def active_moon_count(self, planet_id: str) -> int:
        return self._moon_counts.get(planet_id, 0)


_sup: Supervisor | None = None


def get_supervisor() -> Supervisor:
    global _sup
    if _sup is None:
        _sup = Supervisor()
    return _sup


def reset_supervisor_for_tests() -> Supervisor:
    """Fresh Supervisor instance — prevents crash-count / Moon-concurrency
    state leaking between tests via the module-level singleton. Added
    alongside mcp_client.py's move to using the shared Supervisor for MCP
    crash-loop tracking, which previously reset naturally per-test as a
    side effect of tracking state on now-discarded MCPServer objects."""
    global _sup
    _sup = Supervisor()
    return _sup
