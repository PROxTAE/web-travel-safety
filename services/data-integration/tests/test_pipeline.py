"""Golden cases for corridor geometry, temporal alignment, dedup/conflict, features, gate and immutability."""

from datetime import timedelta

import pytest
from shapely.geometry import Point
from sta_contracts.enums import DataStatus, DisasterEventType, QualityGate, Severity, SourceAuthority, TravelMode
from sta_contracts.models import TravelWindow

from app.pipeline.build_snapshot import build_snapshot, input_hash
from app.pipeline.deduplicate import deduplicate
from app.pipeline.enrich_geospatial import build_corridor, intersect_events, match_weather, overlaps
from app.pipeline.validate import validate_context
from tests.conftest import BKK, CNX, NOW, context, event, straight_route, travel_request, wx


# ------------------------------------------------------------------ corridor (hand-calculated)
def test_corridor_buffer_width_is_metres_not_degrees():
    r = straight_route()
    c = build_corridor(r, buffer_m=15_000, densify_m=5_000)
    # BKK->CNX straight line ~ 580 km; densified at 5 km => >= 116 points
    assert c.length_m == pytest.approx(580_000, rel=0.03)
    assert len(r.geometry.coordinates) == 6 and len(c.h3_cells) >= 3
    # a point 10 km east of the midpoint (approx 0.09 deg lon at lat 16) must be inside, 30 km must be outside
    mid_lon, mid_lat = (BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2
    from shapely.geometry import Point as ShapelyPoint
    from shapely.geometry import shape

    poly = shape(c.polygon_wgs.model_dump())
    assert poly.contains(ShapelyPoint(mid_lon + 0.09, mid_lat))
    assert not poly.contains(ShapelyPoint(mid_lon + 0.30, mid_lat))


def test_event_inside_corridor_and_time_window():
    r = straight_route()
    c = build_corridor(r, buffer_m=15_000, densify_m=5_000)
    window = TravelWindow(departure_at=NOW, arrival_at=NOW + timedelta(hours=8), timezone="Asia/Bangkok")
    mid = [(BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2]
    near = event("usgs:near", [mid[0] + 0.05, mid[1]])  # ~5 km off the line
    far = event("usgs:far", [mid[0] + 2.5, mid[1]], severity=Severity.MINOR)  # ~260 km (MINOR buffer 30 km)
    old = event("usgs:old", mid, effective=NOW - timedelta(days=10))  # earthquake active window 72h => expired
    hits = {h.event.event_id: h for h in intersect_events(c, [near, far, old], window)}
    assert hits["usgs:near"].inside_corridor and hits["usgs:near"].time_overlap
    assert hits["usgs:near"].distance_to_route_m < 8_000
    assert not hits["usgs:far"].inside_corridor
    assert hits["usgs:old"].inside_corridor and not hits["usgs:old"].time_overlap


def test_event_area_of_effect_scales_with_severity():
    r = straight_route()
    c = build_corridor(r, buffer_m=15_000, densify_m=5_000)
    window = TravelWindow(departure_at=NOW, arrival_at=NOW + timedelta(hours=8), timezone="Asia/Bangkok")
    mid = [(BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2]
    # 100 km east: EXTREME quake (300 km radius) reaches corridor; MINOR (30 km) does not
    extreme = event("gdacs:x", [mid[0] + 0.95, mid[1]], severity=Severity.EXTREME, provider="gdacs")
    minor = event("gdacs:m", [mid[0] + 0.95, mid[1]], severity=Severity.MINOR, provider="gdacs")
    hits = {h.event.event_id: h for h in intersect_events(c, [extreme, minor], window)}
    assert hits["gdacs:x"].inside_corridor and not hits["gdacs:m"].inside_corridor


def test_time_overlap_semantics():
    window = TravelWindow(departure_at=NOW, arrival_at=NOW + timedelta(hours=8), timezone="Asia/Bangkok")
    assert overlaps(window, event("a", BKK, effective=NOW + timedelta(hours=3)))  # starts during trip
    assert not overlaps(window, event("b", BKK, effective=NOW + timedelta(hours=9)))  # starts after arrival
    assert not overlaps(window, event("c", BKK, effective=NOW - timedelta(hours=5), ends=NOW - timedelta(hours=1)))
    assert overlaps(window, event("d", BKK, effective=None))  # unknown timing: conservative


def test_weather_matching_tolerance_and_coverage():
    r = straight_route()
    c = build_corridor(r, buffer_m=15_000, densify_m=5_000)
    pts = [wx(coord, NOW + timedelta(hours=8 * i / 5), idx=i) for i, coord in enumerate(r.geometry.coordinates)]
    # one point far in time (12 h off) and one far in space (200 km east) must be ignored
    pts.append(wx(r.geometry.coordinates[2], NOW + timedelta(hours=20), idx=2))
    pts.append(wx([r.geometry.coordinates[2][0] + 2, r.geometry.coordinates[2][1]], NOW + timedelta(hours=3), idx=2))
    matches, coverage = match_weather(c, r, pts, departure_at=NOW, max_distance_m=40_000, tolerance_s=3 * 3600)
    # 6 points over 580 km vs 12 expected positions at 50 km spacing -> 0.5 coverage
    assert len(matches) == 6 and coverage == 0.5
    # only the first two samples present -> 2/12
    m2, cov2 = match_weather(c, r, pts[:2], departure_at=NOW, max_distance_m=40_000, tolerance_s=3 * 3600)
    assert len(m2) == 2 and cov2 == pytest.approx(2 / 12, abs=0.01)
    # dense sampling (13 points) reaches full coverage
    dense = straight_route(n=13)
    pts13 = [wx(coord, NOW + timedelta(hours=8 * i / 12), idx=i) for i, coord in enumerate(dense.geometry.coordinates)]
    _, cov3 = match_weather(
        build_corridor(dense, buffer_m=15_000, densify_m=5_000),
        dense,
        pts13,
        departure_at=NOW,
        max_distance_m=40_000,
        tolerance_s=3 * 3600,
    )
    assert cov3 == 1.0


# ------------------------------------------------------------------ dedup / conflicts
def test_dedup_keeps_official_and_records_conflicts():
    mid = [(BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2]
    usgs = event("usgs:q1", mid, severity=Severity.MODERATE, provider="usgs")
    gdacs = event(
        "gdacs:EQ:1",
        [mid[0] + 0.05, mid[1]],
        severity=Severity.SEVERE,
        provider="gdacs",
        authority=SourceAuthority.INTERGOVERNMENTAL,
    )
    dup = event("usgs:q1", mid, severity=Severity.MODERATE, provider="usgs")  # exact duplicate
    res = deduplicate([usgs, gdacs, dup])
    ids = [e.event_id for e in res.events]
    assert ids.count("usgs:q1") == 1
    # both official records of different authorities are kept; conflict recorded, not averaged
    assert "gdacs:EQ:1" in ids
    assert res.conflicts and res.conflicts[0].field_path == "severity"
    assert res.safety_critical_conflicts == 1
    rep = next(e for e in res.events if e.event_id == "usgs:q1")
    # monotonic safety: representative severity raised to the max official severity in the cluster
    assert rep.severity == Severity.SEVERE
    assert "CONFLICTING" in [f.value for f in rep.quality.flags]


def test_closure_conflict_resolves_to_closed():
    mid = [(BKK[0] + CNX[0]) / 2, (BKK[1] + CNX[1]) / 2]
    a = event(
        "gdacs:FL:9",
        mid,
        etype=DisasterEventType.FLOOD,
        closure=True,
        provider="gdacs",
        authority=SourceAuthority.INTERGOVERNMENTAL,
        severity=Severity.SEVERE,
    )
    b = event(
        "eonet:E9",
        mid,
        etype=DisasterEventType.FLOOD,
        closure=False,
        provider="eonet",
        official=False,
        severity=Severity.SEVERE,
        authority=SourceAuthority.INTERGOVERNMENTAL,
    )
    res = deduplicate([a, b])
    rep = res.events[0]
    assert rep.closure is True and any(c.field_path == "closure" for c in res.conflicts)


# ------------------------------------------------------------------ validation / quarantine
def test_invalid_records_go_to_quarantine(settings):
    req = travel_request()
    bad_wx = wx(BKK, NOW, idx=0, precip=-5.0)
    ctx = context(req, weather=[bad_wx, wx(CNX, NOW + timedelta(hours=8), idx=5)])
    v = validate_context(ctx, request_id=str(req.request_id), trip_id=str(req.trip_id))
    assert len(v.weather) == 1 and len(v.quarantine) == 1
    assert v.quarantine[0].error_code == "NEGATIVE_PRECIPITATION" and len(v.quarantine[0].content_hash) == 64


def test_request_id_mismatch_rejected(settings):
    req = travel_request()
    other = travel_request()
    ctx = context(other)
    with pytest.raises(ValueError):
        validate_context(ctx, request_id=str(req.request_id), trip_id=str(req.trip_id))


# ------------------------------------------------------------------ snapshot end-to-end
def test_snapshot_pass_gate_and_features(settings, schema):
    req = travel_request()
    ctx = context(req)
    res = build_snapshot(req, ctx, settings, schema, now=NOW)
    snap = res.snapshot
    assert snap.quality_summary.gate == QualityGate.PASS
    f = snap.features
    assert f["route_distance_km"] == 580.0 and f["route_duration_hours"] == 8.0
    assert f["travel_mode_encoded"] == 3 and f["route_geometry_inferred"] == 0
    assert f["weather_coverage_ratio"] == 0.5 and f["active_disaster_event_count"] == 0
    assert f["departure_hour_local"] == 9 and f["month"] == 9  # 02:00Z == 09:00 Asia/Bangkok
    assert f["missing_critical_count"] == 0 and f["weather_max_precip_mm_missing"] == 0
    assert snap.route_corridor_geojson is not None and snap.corridor_buffer_m == 15_000
    assert snap.route_candidates[0].usable and snap.route_candidates[0].exposure.score == 0.0
    assert len(snap.content_hash) == 64 and snap.source_ids


def test_snapshot_is_deterministic_and_input_hash_stable(settings, schema):
    req = travel_request()
    ctx = context(req)
    a = build_snapshot(req, ctx, settings, schema, now=NOW)
    b = build_snapshot(req, ctx, settings, schema, now=NOW)
    assert a.snapshot.content_hash == b.snapshot.content_hash
    assert a.input_content_hash == b.input_content_hash == input_hash(req, ctx, settings)
    assert a.snapshot.snapshot_id != b.snapshot.snapshot_id  # ids differ, content identical


def test_severe_weather_and_closure_change_exposure_and_gate(settings, schema):
    req = travel_request()
    r = straight_route()
    mid = r.geometry.coordinates[3]
    weather = [
        wx(
            c,
            NOW + timedelta(hours=8 * i / 5),
            idx=i,
            severity=Severity.SEVERE if i == 3 else Severity.INFO,
            precip=35.0 if i == 3 else 0.0,
            gust=95.0 if i == 3 else 5.0,
        )
        for i, c in enumerate(r.geometry.coordinates)
    ]
    closure = event(
        "gdacs:FL:1",
        mid,
        etype=DisasterEventType.FLOOD,
        severity=Severity.SEVERE,
        closure=True,
        provider="gdacs",
        authority=SourceAuthority.INTERGOVERNMENTAL,
    )
    ctx = context(req, routes=[r], weather=weather, events=[closure])
    snap = build_snapshot(req, ctx, settings, schema, now=NOW).snapshot
    route = snap.route_candidates[0]
    assert route.exposure.closed and not route.usable and route.exposure.score == 1.0
    assert route.exposure.max_hazard_severity == Severity.SEVERE
    assert snap.features["official_closure_count"] == 1 and snap.features["closed_segment_count"] == 1
    assert snap.features["weather_max_precip_mm"] == 35.0 and snap.features["weather_max_wind_gust_kmh"] == 95.0
    assert snap.features["severe_weather_exposure_minutes"] == pytest.approx(40.0)  # 480 min / 12 expected
    assert snap.quality_summary.gate == QualityGate.BLOCK  # only route is closed => no usable route
    assert "NO_USABLE_ROUTE" in snap.quality_summary.reasons
    assert snap.official_alerts and snap.official_alerts[0].event_id == "gdacs:FL:1"


def test_inferred_route_and_missing_weather_degrade(settings, schema):
    req = travel_request(modes=(TravelMode.TRAIN,))
    r = straight_route(mode=TravelMode.TRAIN, inferred=True)
    ctx = context(
        req,
        routes=[r],
        weather=[],
        degraded=["open_meteo: PROVIDER_TIMEOUT"],
        unavailable=["openrouteservice: ORS_API_KEY not configured"],
    )
    snap = build_snapshot(req, ctx, settings, schema, now=NOW).snapshot
    assert snap.quality_summary.gate == QualityGate.DEGRADED
    assert "ROUTE_GEOMETRY_INFERRED" in snap.quality_summary.reasons
    assert any(x.startswith("WEATHER_COVERAGE_LOW") for x in snap.quality_summary.reasons)
    assert snap.quality_summary.weather.status == DataStatus.UNAVAILABLE
    assert snap.features["weather_max_precip_mm"] is None and snap.features["weather_max_precip_mm_missing"] == 1
    assert snap.features["missing_critical_count"] >= 2
    assert "open_meteo: PROVIDER_TIMEOUT" in snap.quality_summary.degraded_services


def test_no_routes_is_insufficient_evidence(settings, schema):
    req = travel_request()
    ctx = context(req, routes=[], weather=[])
    with pytest.raises(ValueError):
        build_snapshot(req, ctx, settings, schema, now=NOW)


def test_dateline_route_corridor_is_sane(settings):
    # Fiji (178E) -> Samoa (172W) crosses the antimeridian; AEQD around the centre keeps it one polygon
    from pyproj import Geod

    g = Geod(ellps="WGS84")
    inner = g.npts(178.4, -18.1, -171.8, -13.8, 3)
    r = straight_route(a=[178.4, -18.1], b=[-171.8, -13.8], n=2)
    r.geometry.coordinates = [[178.4, -18.1], *[[float(x), float(y)] for x, y in inner], [-171.8, -13.8]]
    c = build_corridor(r, buffer_m=60_000, densify_m=20_000)
    from shapely.geometry import shape

    poly = shape(c.polygon_wgs.model_dump())
    assert poly.is_valid and c.length_m == pytest.approx(1_180_000, rel=0.1)
    assert c.polygon_wgs.type == "MultiPolygon" and len(c.polygon_wgs.coordinates) == 2
    for part in c.polygon_wgs.coordinates:
        for ring in part:
            assert all(-180 <= x <= 180 for x, _y in ring)
    # the geodesic midpoint of the route lies right at the antimeridian and must be inside the split corridor
    mx, my = g.npts(178.4, -18.1, -171.8, -13.8, 1)[0]
    assert poly.contains(Point(mx, my))


def test_feature_schema_guard_rejects_unknown(schema):
    from app.domain.features import feature_names

    names = feature_names(schema)
    assert "route_distance_km" in names and len(names) == 31
    assert set(schema["critical"]) <= set(names)
