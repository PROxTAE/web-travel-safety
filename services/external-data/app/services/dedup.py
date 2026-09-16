"""Preliminary cross-source dedup for disaster events.

Rules (04 plan Phase 3): match by provider record IDs / content hash only; never drop a conflicting
official record. Cross-provider near-duplicates (e.g. USGS + GDACS for the same quake) are kept
separately but linked by ``quality.notes`` so คน 5 can resolve with full evidence.
"""

from __future__ import annotations

from datetime import timedelta

from pyproj import Geod
from sta_contracts.enums import DisasterEventType
from sta_contracts.models import DisasterEvent

GEOD = Geod(ellps="WGS84")


def dedup_events(events: list[DisasterEvent]) -> list[DisasterEvent]:
    seen: dict[str, DisasterEvent] = {}
    for e in events:
        prior = seen.get(e.event_id)
        if prior is None:
            seen[e.event_id] = e
            continue
        # same provider record: keep the most recently updated version
        if (
            (e.updated_at or e.effective_at or 0)
            and prior.updated_at
            and e.updated_at
            and e.updated_at > prior.updated_at
        ):
            seen[e.event_id] = e
    out = list(seen.values())
    # link probable cross-source duplicates (same type, < 50 km, < 2 h apart)
    for i, a in enumerate(out):
        for b in out[i + 1 :]:
            if a.source.provider == b.source.provider or a.event_type != b.event_type:
                continue
            if a.event_type != DisasterEventType.EARTHQUAKE:
                continue
            if a.geometry.type != "Point" or b.geometry.type != "Point":
                continue
            _, _, d = GEOD.inv(
                a.geometry.coordinates[0],
                a.geometry.coordinates[1],
                b.geometry.coordinates[0],
                b.geometry.coordinates[1],
            )
            if d > 50_000:
                continue
            if a.effective_at and b.effective_at and abs(a.effective_at - b.effective_at) > timedelta(hours=2):
                continue
            a.quality.notes.append(f"possible duplicate of {b.event_id}")
            b.quality.notes.append(f"possible duplicate of {a.event_id}")
    return out
