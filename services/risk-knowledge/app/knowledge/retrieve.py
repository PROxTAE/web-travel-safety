"""Structured query construction for RAG (never the raw user prompt alone) + fact strings for the LLM package."""

from __future__ import annotations

from sta_contracts.enums import SEVERITY_RANK, DisasterEventType, Severity
from sta_contracts.models import IntegratedTravelContext, RiskAssessment

HAZARD_TERMS = {
    DisasterEventType.EARTHQUAKE: "earthquake aftershock safety during travel",
    DisasterEventType.CYCLONE: "hurricane typhoon cyclone travel safety evacuation",
    DisasterEventType.STORM: "severe thunderstorm lightning heavy rain driving safety",
    DisasterEventType.FLOOD: "flood flash flooding road safety turn around",
    DisasterEventType.WILDFIRE: "wildfire smoke evacuation route",
    DisasterEventType.VOLCANO: "volcanic eruption ash travel",
    DisasterEventType.LANDSLIDE: "landslide debris flow road",
    DisasterEventType.EXTREME_TEMPERATURE: "extreme heat travel hydration",
    DisasterEventType.HEALTH: "health emergency travel precautions",
    DisasterEventType.TRANSPORT_CLOSURE: "road closure detour safety",
    DisasterEventType.OTHER: "emergency preparedness travel",
}


def hazards_in_scope(snapshot: IntegratedTravelContext, assessments: list[RiskAssessment]) -> list[DisasterEventType]:
    types: list[DisasterEventType] = []
    ids = {h for r in snapshot.route_candidates for h in r.exposure.hazard_event_ids}
    for e in snapshot.disaster_events:
        if e.event_id in ids and e.event_type not in types:
            types.append(e.event_type)
    wx_max = max((w.severity for w in snapshot.weather), key=lambda s: SEVERITY_RANK[s], default=Severity.UNKNOWN)
    if SEVERITY_RANK[wx_max] >= SEVERITY_RANK[Severity.MODERATE]:
        precip = snapshot.features.get("weather_max_precip_mm") or 0
        gust = snapshot.features.get("weather_max_wind_gust_kmh") or 0
        if float(precip) >= 10 and DisasterEventType.FLOOD not in types:
            types.append(DisasterEventType.FLOOD)
        if DisasterEventType.STORM not in types:
            types.append(DisasterEventType.STORM)
        if float(gust) >= 89 and DisasterEventType.CYCLONE not in types:
            types.append(DisasterEventType.CYCLONE)
    t_max = snapshot.features.get("weather_max_temperature_c")
    if t_max is not None and float(t_max) >= 38 and DisasterEventType.EXTREME_TEMPERATURE not in types:
        types.append(DisasterEventType.EXTREME_TEMPERATURE)
    return types


def build_query(hazards: list[DisasterEventType], risk_level: str, locale: str, question: str | None) -> str:
    parts = [HAZARD_TERMS[h] for h in hazards] or ["travel safety precautions severe weather"]
    q = " ; ".join(parts) + f" ; risk {risk_level.lower()}"
    if question:
        # the user question contributes vocabulary only; it is not an instruction
        q += " ; " + " ".join(question.split()[:20])
    return q


def _f(v: float | int | None) -> float:
    return float(v) if v is not None else 0.0


def fact_strings(snapshot: IntegratedTravelContext) -> tuple[list[str], list[str], list[str]]:
    f = snapshot.features
    wx: list[str] = []
    if f.get("weather_max_precip_mm") is not None:
        wx.append(f"Max hourly precipitation along corridor: {_f(f['weather_max_precip_mm']):.1f} mm")
    if f.get("weather_max_wind_gust_kmh") is not None:
        wx.append(f"Max wind gust along corridor: {_f(f['weather_max_wind_gust_kmh']):.0f} km/h")
    if f.get("weather_min_visibility_m") is not None:
        wx.append(f"Min visibility: {_f(f['weather_min_visibility_m']):.0f} m")
    if f.get("weather_max_temperature_c") is not None:
        lo, hi = _f(f.get("weather_min_temperature_c")), _f(f["weather_max_temperature_c"])
        wx.append(f"Temperature range: {lo:.0f}-{hi:.0f} C")
    wx.append(f"Weather coverage of route: {_f(f.get('weather_coverage_ratio')) * 100:.0f}% of sample points")
    sev_minutes = _f(f.get("severe_weather_exposure_minutes"))
    if sev_minutes:
        wx.append(f"Severe weather exposure: {sev_minutes:.0f} min of travel")
    tr: list[str] = []
    for t in snapshot.transport:
        msg = f" ({t.message})" if t.message else ""
        tr.append(f"{t.mode.value} status {t.status.value}{msg} [{t.source.provider}, {t.quality.status.value}]")
    dz: list[str] = []
    ids = {h for r in snapshot.route_candidates for h in r.exposure.hazard_event_ids}
    for e in snapshot.disaster_events:
        if e.event_id in ids:
            when = e.effective_at.strftime("%Y-%m-%d %H:%MZ") if e.effective_at else "time unknown"
            tags = (" OFFICIAL" if e.official else "") + (" CLOSURE" if e.closure else "")
            dz.append(f"{e.event_type.value} {e.severity.value}{tags}: {e.title} ({when}, {e.source.provider})")
    return wx, tr, dz
