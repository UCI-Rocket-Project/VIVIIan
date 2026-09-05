"""Structured event log.

The design's log has a severity, a source tag and a message, and lets the
operator filter by severity. ``Dispatcher.log`` is a deque of free text, so the
structure has to come from somewhere.

Rather than change the engine, this wraps ``Dispatcher._log`` at runtime -- the
same technique ``procedures.operations.attach_manual_listener`` already uses on
the valve buttons. GUI2.1 keeps printing exactly what it printed before; GUI3.0
gets a classified copy on the side.
"""
from __future__ import annotations

import time
from collections import deque

import theme as T

INFO, OK, WARN, CRIT = "info", "ok", "warn", "crit"

SEVERITY_COLOR = {
    INFO: T.INK2,
    OK: T.ACID,
    WARN: T.ALERT,
    CRIT: T.CRIT,
}

SEVERITY_ROW = {
    INFO: 0,
    OK: 0,
    WARN: T.WARN_ROW,
    CRIT: T.CRIT_ROW,
}

# Ordered: the first phrase that matches wins, so the more serious readings are
# listed before the general ones.
_RULES: tuple[tuple[str, str, str], ...] = (
    ("validate:", CRIT, "AUDIT"),
    ("panic", CRIT, "PANIC"),
    ("abort", CRIT, "ABORT"),
    ("failed", CRIT, "FAIL"),
    ("failure", CRIT, "FAIL"),
    ("halted", CRIT, "HALT"),
    ("overpressure", CRIT, "LIMIT"),
    ("mismatch", WARN, "LIMIT"),
    ("refusing", WARN, "GUARD"),
    ("watchdog", WARN, "GUARD"),
    ("timeout", WARN, "GUARD"),
    ("timed out", WARN, "GUARD"),
    ("suspend", WARN, "MANUAL"),
    ("manual", WARN, "MANUAL"),
    ("ambiguous", WARN, "TIE"),
    ("two transitions", WARN, "TIE"),
    ("stale", WARN, "FEED"),
    ("disarm", WARN, "ARM"),
    ("armed", OK, "ARM"),
    ("forced", WARN, "FORCE"),
    ("complete", OK, "PROC"),
    ("done", OK, "PROC"),
    ("verified", OK, "PROC"),
    ("pass", OK, "PROC"),
    ("resume", OK, "PROC"),
    ("entering", INFO, "SEQ"),
    ("initial state", INFO, "SEQ"),
    ("state", INFO, "SEQ"),
    ("operation", INFO, "OP"),
)


def classify(message: str) -> tuple[str, str]:
    """(severity, source tag) for one engine log line."""
    text = message.lower()
    for needle, severity, source in _RULES:
        if needle in text:
            return severity, source
    return INFO, "SM"


class Event:
    __slots__ = ("stamp", "source", "severity", "message")

    def __init__(self, stamp: str, source: str, severity: str, message: str) -> None:
        self.stamp = stamp
        self.source = source
        self.severity = severity
        self.message = message

    @property
    def color(self) -> int:
        return SEVERITY_COLOR[self.severity]

    @property
    def row_background(self) -> int:
        return SEVERITY_ROW[self.severity]


class EventLog:
    """Newest first, which is the order the design shows them in."""

    def __init__(self, capacity: int = 400) -> None:
        self.events: deque[Event] = deque(maxlen=capacity)
        self.filters = {INFO: True, OK: True, WARN: True, CRIT: True}

    def add(self, message: str, *, source: str = "", severity: str = "") -> None:
        auto_severity, auto_source = classify(message)
        self.events.appendleft(
            Event(
                stamp=time.strftime("%H:%M:%S"),
                source=source or auto_source,
                severity=severity or auto_severity,
                message=message,
            )
        )

    def count(self, severity: str) -> int:
        return sum(1 for event in self.events if event.severity == severity)

    def visible(self, limit: int) -> list[Event]:
        out = []
        for event in self.events:
            if self.filters.get(event.severity, True):
                out.append(event)
                if len(out) >= limit:
                    break
        return out

    def toggle(self, severity: str) -> None:
        self.filters[severity] = not self.filters.get(severity, True)


def attach(dispatcher, log: EventLog) -> None:
    """Mirror everything the dispatcher logs into *log*.

    Wraps the bound method in place. The engine file is untouched, and GUI2.1
    keeps behaving exactly as before if it is running against the same modules.
    """
    inner = dispatcher._log

    def wrapped(message: str) -> None:
        inner(message)
        # The engine stamps its own lines; keep the raw text and let the panel
        # render our stamp, so the two cannot disagree about ordering.
        log.add(message)

    dispatcher._log = wrapped

    # Anything logged during construction happened before the wrap, so replay it.
    for line in list(dispatcher.log):
        log.add(line.split("  ", 1)[-1] if "  " in line else line)
