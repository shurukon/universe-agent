"""schedule/cron.py — a tiny 5-field cron parser.

Fields: minute hour day-of-month month day-of-week. Supports `*`,
lists (1,2,3), ranges (1-5), steps (*/15, 1-10/2). The classic
dom/dow rule is honored: when BOTH day-of-month and day-of-week are
restricted, a day matches if EITHER does (cron's OR merge) — the bug
class that quietly skews schedules when both are treated as AND.
"""
from __future__ import annotations

import time

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))  # dow: 0=Sun
_NAMES = {
    4: {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6},
    3: {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12},
}


class CronError(ValueError):
    pass


def _parse_field(spec: str, field_idx: int) -> set[int]:
    lo, hi = _RANGES[field_idx]
    names = _NAMES.get(field_idx, {})
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            raise CronError(f"empty field component in {spec!r}")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"bad step {step_s!r} in {spec!r}")
            if step < 1:
                raise CronError(f"step must be >= 1 in {spec!r}")
        if part in ("*", ""):
            base = range(lo, hi + 1)
        elif "-" in part:
            a, b = part.split("-", 1)
            a = str(names.get(a, a))
            b = str(names.get(b, b))
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise CronError(f"bad range {part!r} in {spec!r}")
            if start > end or start < lo or end > hi:
                raise CronError(f"range {part!r} out of bounds in {spec!r}")
            base = range(start, end + 1)
        else:
            p = str(names.get(part, part))
            try:
                v = int(p)
            except ValueError:
                raise CronError(f"unknown value {part!r} in {spec!r}")
            if v < lo or v > hi:
                raise CronError(f"value {v} out of bounds in {spec!r}")
            base = [v]
        values.update(base[::step] if isinstance(base, range) else base)
    return values


class CronSchedule:
    """A parsed 5-field schedule with next-fire computation."""

    def __init__(self, spec: str):
        fields = spec.split()
        if len(fields) != 5:
            raise CronError(
                f"cron needs 5 fields (m h dom mon dow), got {len(fields)}")
        self.minutes = _parse_field(fields[0], 0)
        self.hours = _parse_field(fields[1], 1)
        self.dom = _parse_field(fields[2], 2)
        self.months = _parse_field(fields[3], 3)
        self.dow = _parse_field(fields[4], 4)
        self.dom_restricted = fields[2] != "*"
        self.dow_restricted = fields[4] != "*"
        self.spec = spec

    def matches(self, tm: time.struct_time) -> bool:
        if tm.tm_min not in self.minutes or tm.tm_hour not in self.hours:
            return False
        if tm.tm_mon not in self.months:
            return False
        # dow: Python's tm_wday is Mon=0..Sun=6; cron is Sun=0..Sat=6
        cron_dow = (tm.tm_wday + 1) % 7
        dom_ok = tm.tm_mday in self.dom
        dow_ok = cron_dow in self.dow
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok  # the classic OR merge
        return dom_ok and dow_ok

    def next_after(self, ts: float) -> float:
        """The next fire time (unix epoch) strictly after ts. Scans minute
        by minute — bounded at ~4 years so a pathological spec (e.g.
        Feb 30, which can never fire) raises instead of looping forever."""
        t = int(ts) - (int(ts) % 60) + 60  # start of the next minute
        limit = t + 366 * 4 * 24 * 3600
        while t < limit:
            if self.matches(time.localtime(t)):
                return float(t)
            t += 60
        raise CronError(f"no fire time within 4 years for {self.spec!r}")
