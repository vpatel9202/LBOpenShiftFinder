from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Shift:
    """A scheduled shift from the iCal feed (user's own shifts)."""
    date: str  # YYYY-MM-DD
    start_time: str  # ISO 8601 datetime
    end_time: str  # ISO 8601 datetime
    assignment: str  # e.g. "R15", "A3", "T1"

    @property
    def unique_key(self) -> str:
        return f"{self.date}|{self.start_time}|{self.end_time}|{self.assignment}"


@dataclass
class OpenShift:
    """An open/unassigned shift scraped from Lightning Bolt."""
    date: str  # YYYY-MM-DD
    start_time: str  # ISO 8601 datetime
    end_time: str  # ISO 8601 datetime
    assignment: str  # e.g. "R15", "Night Shift"
    label: str  # e.g. "OPEN 1", "OPEN 2"

    @property
    def unique_key(self) -> str:
        return f"{self.date}|{self.start_time}|{self.end_time}|{self.assignment}|{self.label}"


@dataclass
class SyncedShift:
    """An open shift that has been added to Google Calendar."""
    date: str
    start_time: str
    end_time: str
    assignment: str
    label: str
    google_event_id: str

    @property
    def unique_key(self) -> str:
        return f"{self.date}|{self.start_time}|{self.end_time}|{self.assignment}|{self.label}"

    @classmethod
    def from_open_shift(cls, shift: OpenShift, google_event_id: str) -> SyncedShift:
        return cls(
            date=shift.date,
            start_time=shift.start_time,
            end_time=shift.end_time,
            assignment=shift.assignment,
            label=shift.label,
            google_event_id=google_event_id,
        )


@dataclass
class SyncState:
    """Persisted state tracking which shifts are synced to Google Calendar."""
    last_run: str | None = None
    synced_shifts: list[SyncedShift] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "last_run": self.last_run,
                "synced_shifts": [asdict(s) for s in self.synced_shifts],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> SyncState:
        parsed = json.loads(data)
        return cls(
            last_run=parsed.get("last_run"),
            synced_shifts=[
                SyncedShift(**s) for s in parsed.get("synced_shifts", [])
            ],
        )
