"""GTFS static registry + GTFS-Realtime adapter.

- Feeds come from ``config/providers.yaml`` (bbox coverage, URLs, license). No feed => OUTSIDE_COVERAGE.
- Realtime TripUpdates/Alerts are parsed with the official protobuf bindings.
- A feed whose ``header.timestamp`` is older than the TTL yields STALE/UNKNOWN status, never ON_TIME.
- Static routes.txt is downloaded lazily (cached on a volume with checksum) only to label route names.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from google.transit import gtfs_realtime_pb2
from pydantic import BaseModel, Field
from sta_common.http import ResilientClient
from sta_common.logging import get_logger
from sta_contracts.enums import (
    DataStatus,
    ProviderKind,
    QualityFlag,
    SourceAuthority,
    TransportServiceStatus,
    TravelMode,
)
from sta_contracts.geo import Point
from sta_contracts.models import DataQuality, TransportStatus

from app.adapters.base import BaseAdapter, ProviderDescriptor, ProviderError, ProviderErrorCode
from app.domain.canonical import deterministic_id, provenance

log = get_logger("gtfs")


class GtfsFeed(BaseModel):
    id: str
    agency: str
    country_code: str
    region: str
    bbox: list[float] = Field(min_length=4, max_length=4)
    timezone: str
    modes: list[TravelMode]
    static_url: str
    realtime: dict[str, str]
    license: str
    attribution: str
    refresh_interval_seconds: int = 60
    enabled: bool = True

    def covers(self, p: Point) -> bool:
        return self.bbox[0] <= p.lon <= self.bbox[2] and self.bbox[1] <= p.lat <= self.bbox[3]


class GtfsRegistry(BaseModel):
    config_version: str
    gtfs_feeds: list[GtfsFeed] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> GtfsRegistry:
        p = Path(path)
        if not p.exists():
            return cls(config_version="0.0.0", gtfs_feeds=[])
        return cls.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})

    def feeds_for(self, origin: Point, destination: Point, mode: TravelMode) -> list[GtfsFeed]:
        return [
            f for f in self.gtfs_feeds if f.enabled and mode in f.modes and f.covers(origin) and f.covers(destination)
        ]


ALERT_EFFECT_STATUS: dict[int, TransportServiceStatus] = {
    gtfs_realtime_pb2.Alert.NO_SERVICE: TransportServiceStatus.CANCELLED,
    gtfs_realtime_pb2.Alert.REDUCED_SERVICE: TransportServiceStatus.DISRUPTED,
    gtfs_realtime_pb2.Alert.SIGNIFICANT_DELAYS: TransportServiceStatus.DELAYED,
    gtfs_realtime_pb2.Alert.DETOUR: TransportServiceStatus.DISRUPTED,
    gtfs_realtime_pb2.Alert.ADDITIONAL_SERVICE: TransportServiceStatus.ON_TIME,
    gtfs_realtime_pb2.Alert.MODIFIED_SERVICE: TransportServiceStatus.DISRUPTED,
    gtfs_realtime_pb2.Alert.OTHER_EFFECT: TransportServiceStatus.UNKNOWN,
    gtfs_realtime_pb2.Alert.UNKNOWN_EFFECT: TransportServiceStatus.UNKNOWN,
    gtfs_realtime_pb2.Alert.STOP_MOVED: TransportServiceStatus.DISRUPTED,
    gtfs_realtime_pb2.Alert.NO_EFFECT: TransportServiceStatus.ON_TIME,
    gtfs_realtime_pb2.Alert.ACCESSIBILITY_ISSUE: TransportServiceStatus.DISRUPTED,
}


def _translated(ts: Any) -> str | None:
    if ts is None:
        return None
    for t in ts.translation:
        if t.language in ("en", "en-US", "") or not t.language:
            return str(t.text)
    return str(ts.translation[0].text) if ts.translation else None


class GtfsRealtimeAdapter(BaseAdapter):
    descriptor = ProviderDescriptor(
        name="gtfs_rt",
        kind=ProviderKind.TRANSIT,
        version="gtfs-rt-2.0",
        authority="OFFICIAL",
        license="per feed (see config/providers.yaml)",
        attribution="per feed (see config/providers.yaml)",
        docs_url="https://gtfs.org/documentation/realtime/reference/",
        coverage_note="Only registered agency feeds; see providers.yaml",
    )

    def __init__(self, registry: GtfsRegistry, *, service_name: str, ttl: int, cache_dir: str, **kw: Any) -> None:
        super().__init__(service_name=service_name, **kw)
        self.registry = registry
        self.ttl = ttl
        self.cache_dir = Path(cache_dir)
        self._clients: dict[str, ResilientClient] = {}
        self._route_names: dict[str, dict[str, str]] = {}
        self.descriptor.enabled = any(f.enabled for f in registry.gtfs_feeds)
        self.descriptor.coverage_note = (
            "Registered feeds: " + ", ".join(f"{f.id} ({f.region})" for f in registry.gtfs_feeds if f.enabled)
            if self.descriptor.enabled
            else "UNAVAILABLE: no GTFS feeds registered"
        )

    def _client(self, url: str) -> ResilientClient:
        base = url.rsplit("/", 1)[0]
        if base not in self._clients:
            self._clients[base] = ResilientClient(
                service_name=self.service_name,
                dependency="gtfs_rt",
                base_url=base,
                read_timeout=10,
                total_timeout=12,
                default_headers={"Accept": "application/x-protobuf"},
            )
        return self._clients[base]

    async def status(
        self, origin: Point, destination: Point, mode: TravelMode, *, deadline: float | None = None
    ) -> list[TransportStatus]:
        feeds = self.registry.feeds_for(origin, destination, mode)
        if not feeds:
            raise ProviderError(
                ProviderErrorCode.OUTSIDE_COVERAGE, f"no registered GTFS-RT feed covers this {mode.value} trip"
            )
        out: list[TransportStatus] = []
        for feed in feeds:
            out.extend(await self._feed_status(feed, mode, deadline))
        return out

    async def _fetch_pb(self, url: str, deadline: float | None) -> tuple[Any, bytes]:
        client = self._client(url)
        resp = await self._call(client, "GET", "/" + url.rsplit("/", 1)[1], deadline=deadline)
        msg = gtfs_realtime_pb2.FeedMessage()
        try:
            msg.ParseFromString(resp.content)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                ProviderErrorCode.PROVIDER_SCHEMA_CHANGED, f"GTFS-RT protobuf parse failed: {type(exc).__name__}"
            ) from None
        return msg, resp.content

    async def _feed_status(self, feed: GtfsFeed, mode: TravelMode, deadline: float | None) -> list[TransportStatus]:
        fetched_at = datetime.now(UTC)
        records: list[TransportStatus] = []
        alerts_url = feed.realtime.get("alerts")
        trips_url = feed.realtime.get("trip_updates")
        if alerts_url:
            msg, raw = await self._fetch_pb(alerts_url, deadline)
            records.extend(self.parse_alerts(msg, raw, feed, mode, fetched_at))
        if trips_url:
            msg, raw = await self._fetch_pb(trips_url, deadline)
            records.append(self.summarize_trip_updates(msg, raw, feed, mode, fetched_at))
        return records

    # ---- pure parsers (unit-tested against a real sanitized feed capture) ----
    def parse_alerts(
        self, msg: Any, raw: bytes, feed: GtfsFeed, mode: TravelMode, fetched_at: datetime
    ) -> list[TransportStatus]:
        feed_ts = datetime.fromtimestamp(msg.header.timestamp, tz=UTC) if msg.header.timestamp else None
        stale = feed_ts is None or (fetched_at - feed_ts).total_seconds() > self.ttl
        out: list[TransportStatus] = []
        for entity in msg.entity:
            if not entity.HasField("alert"):
                continue
            a = entity.alert
            status = ALERT_EFFECT_STATUS.get(a.effect, TransportServiceStatus.UNKNOWN)
            if stale:
                status = TransportServiceStatus.UNKNOWN
            routes = sorted({ie.route_id for ie in a.informed_entity if ie.route_id})
            active_now = True
            if a.active_period:
                now_ts = int(fetched_at.timestamp())
                active_now = any(
                    (p.start == 0 or p.start <= now_ts) and (p.end == 0 or now_ts <= p.end) for p in a.active_period
                )
            if not active_now:
                continue
            header = _translated(a.header_text) or "Service alert"
            desc = _translated(a.description_text)
            rec_raw = {
                "id": entity.id,
                "effect": int(a.effect),
                "cause": int(a.cause),
                "routes": routes,
                "header": header,
            }
            out.append(
                TransportStatus(
                    id=deterministic_id("gtfs_rt", feed.id, "alert", entity.id),
                    mode=mode,
                    operator=feed.agency,
                    service_number=",".join(routes)[:64] or None,
                    origin_stop=None,
                    destination_stop=None,
                    status=status,
                    delay_minutes=None,
                    cancellation=status == TransportServiceStatus.CANCELLED,
                    message=(header + (f" — {desc}" if desc else ""))[:500],
                    quality=DataQuality(
                        status=DataStatus.STALE if stale else DataStatus.FRESH,
                        score=0.4 if stale else 0.9,
                        flags=[QualityFlag.STALE] if stale else [],
                        freshness_seconds=int((fetched_at - feed_ts).total_seconds()) if feed_ts else None,
                        notes=[f"feed header timestamp {feed_ts.isoformat() if feed_ts else 'missing'}"],
                    ),
                    source=provenance(
                        provider="gtfs_rt",
                        record_id=f"{feed.id}:alert:{entity.id}",
                        authority=SourceAuthority.OFFICIAL,
                        source_url=feed.realtime.get("alerts"),
                        license_=feed.license,
                        attribution=feed.attribution,
                        observed_at=feed_ts,
                        published_at=feed_ts,
                        fetched_at=fetched_at,
                        ttl_seconds=self.ttl,
                        raw=rec_raw,
                    ),
                )
            )
        return out

    def summarize_trip_updates(
        self, msg: Any, raw: bytes, feed: GtfsFeed, mode: TravelMode, fetched_at: datetime
    ) -> TransportStatus:
        feed_ts = datetime.fromtimestamp(msg.header.timestamp, tz=UTC) if msg.header.timestamp else None
        stale = feed_ts is None or (fetched_at - feed_ts).total_seconds() > self.ttl
        delays: list[int] = []
        cancelled = 0
        trips = 0
        for entity in msg.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            trips += 1
            if tu.trip.schedule_relationship == gtfs_realtime_pb2.TripDescriptor.CANCELED:
                cancelled += 1
                continue
            if tu.HasField("delay"):
                delays.append(int(tu.delay))
            elif tu.stop_time_update:
                stu = tu.stop_time_update[0]
                d = (
                    stu.departure.delay
                    if stu.HasField("departure")
                    else stu.arrival.delay
                    if stu.HasField("arrival")
                    else 0
                )
                delays.append(int(d))
        max_delay_min = round(max(delays) / 60) if delays else None
        delayed_share = (sum(1 for d in delays if d >= 300) / len(delays)) if delays else 0.0
        if stale or trips == 0:
            status = TransportServiceStatus.UNKNOWN
        elif cancelled / max(trips, 1) > 0.2:
            status = TransportServiceStatus.DISRUPTED
        elif delayed_share > 0.3:
            status = TransportServiceStatus.DELAYED
        else:
            status = TransportServiceStatus.ON_TIME  # real-time evidence present: allowed by policy
        rec_raw = {
            "trips": trips,
            "cancelled": cancelled,
            "max_delay_s": max(delays) if delays else None,
            "ts": msg.header.timestamp,
        }
        return TransportStatus(
            id=deterministic_id("gtfs_rt", feed.id, "trip_updates", str(msg.header.timestamp)),
            mode=mode,
            operator=feed.agency,
            service_number=None,
            status=status,
            delay_minutes=max_delay_min,
            cancellation=False,
            message=(f"{trips} live trips, {cancelled} cancelled, {int(delayed_share * 100)}% delayed >=5 min"),
            quality=DataQuality(
                status=DataStatus.STALE if stale else DataStatus.FRESH,
                score=0.4 if stale else 0.85,
                flags=[QualityFlag.STALE] if stale else [],
                coverage=1.0,
                freshness_seconds=int((fetched_at - feed_ts).total_seconds()) if feed_ts else None,
                notes=["aggregate of live trip updates for the agency region; not matched to a specific service"],
            ),
            source=provenance(
                provider="gtfs_rt",
                record_id=f"{feed.id}:trip_updates:{msg.header.timestamp}",
                authority=SourceAuthority.OFFICIAL,
                source_url=feed.realtime.get("trip_updates"),
                license_=feed.license,
                attribution=feed.attribution,
                observed_at=feed_ts,
                published_at=feed_ts,
                fetched_at=fetched_at,
                ttl_seconds=self.ttl,
                raw=rec_raw,
            ),
        )

    # ---- static (lazy, cached) --------------------------------------------
    async def route_names(self, feed: GtfsFeed, *, deadline: float | None = None) -> dict[str, str]:
        if feed.id in self._route_names:
            return self._route_names[feed.id]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = self.cache_dir / f"{feed.id}-routes.csv"
        if not cache.exists():
            client = self._client(feed.static_url)
            resp = await self._call(client, "GET", "/" + feed.static_url.rsplit("/", 1)[1], deadline=deadline)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                data = zf.read("routes.txt")
            cache.write_bytes(data)
            (self.cache_dir / f"{feed.id}-routes.sha256").write_text(hashlib.sha256(resp.content).hexdigest())
        names: dict[str, str] = {}
        for row in csv.DictReader(io.StringIO(cache.read_text(encoding="utf-8-sig"))):
            names[row["route_id"]] = row.get("route_long_name") or row.get("route_short_name") or row["route_id"]
        self._route_names[feed.id] = names
        return names

    async def close(self) -> None:
        for c in self._clients.values():
            await c.aclose()
