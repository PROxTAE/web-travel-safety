"""Stage 3 — deduplicate + Stage 4 — resolve conflicts.

Dedup key order: (1) provider+record id (+updated), (2) content hash, (3) spatial/temporal cluster
(same event type, < cluster_km, < cluster_hours). Cluster members are kept; the representative is the
highest authority then freshest. Official records from different authorities are never merged away —
they stay as separate records linked via ``quality.notes``. Conflicts on safety-critical fields
(severity, closure) are recorded, never averaged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from pyproj import Geod
from shapely.geometry import shape
from sta_contracts.enums import AUTHORITY_RANK, SEVERITY_RANK, QualityFlag
from sta_contracts.models import DataConflict, DisasterEvent

GEOD = Geod(ellps="WGS84")
CLUSTER_KM = 50.0
CLUSTER_HOURS = 3.0


@dataclass(slots=True)
class DedupResult:
    events: list[DisasterEvent]
    clusters: list[list[str]] = field(default_factory=list)
    conflicts: list[DataConflict] = field(default_factory=list)
    safety_critical_conflicts: int = 0


def _centroid(e: DisasterEvent) -> tuple[float, float]:
    g = shape(e.geometry.model_dump())
    c = g.centroid
    return float(c.x), float(c.y)


def _distance_km(a: DisasterEvent, b: DisasterEvent) -> float:
    (x1, y1), (x2, y2) = _centroid(a), _centroid(b)
    _, _, d = GEOD.inv(x1, y1, x2, y2)
    return float(d) / 1000


def _time(e: DisasterEvent):  # type: ignore[no-untyped-def]
    return e.effective_at or e.updated_at


def _rank(e: DisasterEvent) -> tuple[int, float, float]:
    ts = _time(e)
    return (AUTHORITY_RANK[e.source.authority], ts.timestamp() if ts else 0.0, e.quality.score)


def deduplicate(events: list[DisasterEvent]) -> DedupResult:
    # 1) exact provider record id / content hash
    by_key: dict[str, DisasterEvent] = {}
    for e in events:
        key = e.event_id
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = e
            continue
        t_new = e.updated_at or e.effective_at
        t_old = prior.updated_at or prior.effective_at
        if t_new is not None and t_old is not None and t_new > t_old:
            by_key[key] = e
    by_hash: dict[str, DisasterEvent] = {}
    for e in by_key.values():
        by_hash.setdefault(e.source.content_hash, e)
    unique = list(by_hash.values())

    # 2) spatial/temporal clusters across providers (same type only)
    result = DedupResult(events=[])
    assigned: dict[str, int] = {}
    clusters: list[list[DisasterEvent]] = []
    for e in unique:
        placed = False
        for ci, members in enumerate(clusters):
            rep = members[0]
            if rep.event_type != e.event_type or rep.source.provider == e.source.provider:
                continue
            if _distance_km(rep, e) > CLUSTER_KM:
                continue
            ta, tb = _time(rep), _time(e)
            if ta and tb and abs(ta - tb) > timedelta(hours=CLUSTER_HOURS):
                continue
            members.append(e)
            assigned[e.event_id] = ci
            placed = True
            break
        if not placed:
            assigned[e.event_id] = len(clusters)
            clusters.append([e])

    for members in clusters:
        members.sort(key=_rank, reverse=True)
        rep = members[0]
        if len(members) > 1:
            ids = [m.event_id for m in members]
            result.clusters.append(ids)
            for m in members:
                m.quality.notes.append("cluster: " + ",".join(i for i in ids if i != m.event_id))
            # conflicts on safety-critical fields are recorded, never averaged
            sev = {m.severity for m in members}
            if len(sev) > 1:
                result.conflicts.append(
                    DataConflict(
                        field_path="severity",
                        values=[s.value for s in sev],
                        source_ids=[m.source.source_id for m in members],
                        resolution=f"kept {rep.severity.value} from {rep.source.provider} (authority/freshness rank); "
                        f"max severity retained on representative",
                    )
                )
                result.safety_critical_conflicts += 1
                # monotonic safety: representative never shows a lower severity than any official member
                max_official = max(
                    (m.severity for m in members if m.official), key=lambda s: SEVERITY_RANK[s], default=None
                )
                if max_official and SEVERITY_RANK[max_official] > SEVERITY_RANK[rep.severity]:
                    rep.severity = max_official
                    rep.quality.flags.append(QualityFlag.CONFLICTING)
            closure = {m.closure for m in members}
            if len(closure) > 1:
                result.conflicts.append(
                    DataConflict(
                        field_path="closure",
                        values=[True, False],
                        source_ids=[m.source.source_id for m in members],
                        resolution="closure=True retained (official priority)",
                    )
                )
                result.safety_critical_conflicts += 1
                rep.closure = True
                rep.quality.flags.append(QualityFlag.CONFLICTING)
            for m in members[1:]:
                m.quality.notes.append(f"superseded_by:{rep.event_id}")
        # official records of *different* authorities are all kept (never merged away)
        result.events.append(rep)
        for m in members[1:]:
            if m.official and m.source.authority != rep.source.authority:
                result.events.append(m)
    return result
