"""Deterministic localized explanation templates (Thai/English) built from reason codes only."""

from __future__ import annotations

from sta_contracts.enums import ActionCode, ReasonCode, RiskLevel

from app.llm.schemas import Explanation

SUMMARY = {
    "en": {
        ActionCode.NORMAL: "Conditions along your route look acceptable. Travel as planned and keep checking updates.",
        ActionCode.CHANGE_ROUTE: "A safer route is available. Use the recommended alternative.",
        ActionCode.DELAY: "Risk is expected to decrease if you leave later. Consider delaying departure.",
        ActionCode.AVOID: "Travel is not advised right now: risk is high or an official restriction applies.",
    },
    "th": {
        ActionCode.NORMAL: "สภาพเส้นทางอยู่ในระดับที่ยอมรับได้ เดินทางตามแผนและติดตามข้อมูลต่อเนื่อง",
        ActionCode.CHANGE_ROUTE: "มีเส้นทางที่ปลอดภัยกว่า แนะนำให้ใช้เส้นทางทางเลือกที่ระบบแนะนำ",
        ActionCode.DELAY: "ความเสี่ยงมีแนวโน้มลดลงหากออกเดินทางช้ากว่าเดิม แนะนำให้เลื่อนเวลาออกเดินทาง",
        ActionCode.AVOID: "ยังไม่แนะนำให้เดินทางในขณะนี้ เนื่องจากความเสี่ยงสูงหรือมีประกาศจำกัดจากทางการ",
    },
}
REASON = {
    "en": {
        ReasonCode.SEVERE_WEATHER_CORRIDOR: "Severe weather is forecast on part of the route in your travel window.",
        ReasonCode.HEAVY_PRECIPITATION: "Heavy precipitation is forecast on the corridor.",
        ReasonCode.HIGH_WIND: "Strong wind gusts are forecast on the corridor.",
        ReasonCode.LOW_VISIBILITY: "Low visibility is forecast on the corridor.",
        ReasonCode.EXTREME_TEMPERATURE: "Extreme temperatures are forecast on the corridor.",
        ReasonCode.EARTHQUAKE_NEAR_CORRIDOR: "A recent earthquake occurred near the route.",
        ReasonCode.ACTIVE_DISASTER_EVENT: "An active hazard event intersects the route corridor.",
        ReasonCode.OFFICIAL_ALERT_ACTIVE: "An official alert is active on the route corridor.",
        ReasonCode.OFFICIAL_CLOSURE: "An official closure affects the original route.",
        ReasonCode.TRANSPORT_DISRUPTION: "Transport services on this trip are disrupted.",
        ReasonCode.TRANSPORT_DELAY: "Transport delays are reported for this trip.",
        ReasonCode.SAFER_ROUTE_AVAILABLE: "An alternative route with materially lower risk is available.",
        ReasonCode.NO_SAFER_ROUTE: "No alternative route with acceptable risk was found.",
        ReasonCode.RISK_DECREASES_LATER: "Forecast risk is lower for a later departure.",
        ReasonCode.LOW_DATA_COVERAGE: "Data coverage for this route is limited.",
        ReasonCode.STALE_DATA: "Some data sources are older than their freshness limit.",
        ReasonCode.CONFLICTING_SOURCES: "Sources disagree on a safety-critical detail.",
        ReasonCode.INSUFFICIENT_EVIDENCE: "There is not enough evidence to rate this route as low risk.",
        ReasonCode.NO_RELIABLE_KNOWLEDGE_EVIDENCE: "No approved guidance document matched this situation.",
        ReasonCode.MODEL_UNAVAILABLE: "The local risk model was unavailable; a rule-based assessment was used.",
        ReasonCode.PROVIDER_UNAVAILABLE: "A data provider was unavailable.",
        ReasonCode.CONDITIONS_NORMAL: "No significant hazards were found on the corridor.",
    },
    "th": {
        ReasonCode.SEVERE_WEATHER_CORRIDOR: "พยากรณ์อากาศรุนแรงในบางช่วงของเส้นทางระหว่างเวลาเดินทาง",
        ReasonCode.HEAVY_PRECIPITATION: "พยากรณ์ฝนตกหนักบนเส้นทาง",
        ReasonCode.HIGH_WIND: "พยากรณ์ลมกระโชกแรงบนเส้นทาง",
        ReasonCode.LOW_VISIBILITY: "พยากรณ์ทัศนวิสัยต่ำบนเส้นทาง",
        ReasonCode.EXTREME_TEMPERATURE: "พยากรณ์อุณหภูมิสุดขั้วบนเส้นทาง",
        ReasonCode.EARTHQUAKE_NEAR_CORRIDOR: "เกิดแผ่นดินไหวเมื่อไม่นานมานี้ใกล้เส้นทาง",
        ReasonCode.ACTIVE_DISASTER_EVENT: "มีเหตุภัยพิบัติที่ยังดำเนินอยู่ตัดผ่านแนวเส้นทาง",
        ReasonCode.OFFICIAL_ALERT_ACTIVE: "มีประกาศเตือนจากทางการบนแนวเส้นทาง",
        ReasonCode.OFFICIAL_CLOSURE: "มีประกาศปิดเส้นทางจากทางการกระทบเส้นทางเดิม",
        ReasonCode.TRANSPORT_DISRUPTION: "บริการขนส่งของทริปนี้หยุดชะงัก",
        ReasonCode.TRANSPORT_DELAY: "มีรายงานความล่าช้าของบริการขนส่ง",
        ReasonCode.SAFER_ROUTE_AVAILABLE: "มีเส้นทางทางเลือกที่ความเสี่ยงต่ำกว่าอย่างมีนัยสำคัญ",
        ReasonCode.NO_SAFER_ROUTE: "ไม่พบเส้นทางทางเลือกที่มีความเสี่ยงยอมรับได้",
        ReasonCode.RISK_DECREASES_LATER: "ความเสี่ยงตามพยากรณ์ต่ำกว่าหากออกเดินทางช้าลง",
        ReasonCode.LOW_DATA_COVERAGE: "ข้อมูลครอบคลุมเส้นทางนี้จำกัด",
        ReasonCode.STALE_DATA: "แหล่งข้อมูลบางส่วนเก่ากว่าเกณฑ์ความสด",
        ReasonCode.CONFLICTING_SOURCES: "แหล่งข้อมูลขัดแย้งกันในรายละเอียดที่สำคัญต่อความปลอดภัย",
        ReasonCode.INSUFFICIENT_EVIDENCE: "หลักฐานไม่เพียงพอที่จะประเมินว่าเส้นทางนี้ความเสี่ยงต่ำ",
        ReasonCode.NO_RELIABLE_KNOWLEDGE_EVIDENCE: "ไม่พบเอกสารแนะนำที่ได้รับอนุมัติตรงกับสถานการณ์นี้",
        ReasonCode.MODEL_UNAVAILABLE: "โมเดลประเมินความเสี่ยงไม่พร้อมใช้งาน ระบบใช้กฎพื้นฐานแทน",
        ReasonCode.PROVIDER_UNAVAILABLE: "ผู้ให้บริการข้อมูลบางรายไม่พร้อมใช้งาน",
        ReasonCode.CONDITIONS_NORMAL: "ไม่พบภัยที่มีนัยสำคัญบนแนวเส้นทาง",
    },
}
ACTIONS = {
    "en": {
        ActionCode.NORMAL: ["Check conditions again before departure.", "Keep live alerts on for this trip."],
        ActionCode.CHANGE_ROUTE: [
            "Apply the recommended route in the app.",
            "Review the trade-offs (time, distance) before confirming.",
        ],
        ActionCode.DELAY: [
            "Delay departure and re-check the forecast before leaving.",
            "Keep live alerts on for this trip.",
        ],
        ActionCode.AVOID: [
            "Do not start the trip until conditions or official restrictions change.",
            "Follow local authorities' instructions.",
        ],
    },
    "th": {
        ActionCode.NORMAL: ["ตรวจสอบสภาพอากาศอีกครั้งก่อนออกเดินทาง", "เปิดการแจ้งเตือนสดสำหรับทริปนี้"],
        ActionCode.CHANGE_ROUTE: ["กดใช้เส้นทางที่แนะนำในแอป", "ตรวจสอบข้อแลกเปลี่ยน (เวลา ระยะทาง) ก่อนยืนยัน"],
        ActionCode.DELAY: ["เลื่อนเวลาออกเดินทางและตรวจพยากรณ์อีกครั้งก่อนออก", "เปิดการแจ้งเตือนสดสำหรับทริปนี้"],
        ActionCode.AVOID: ["อย่าเพิ่งเริ่มเดินทางจนกว่าสภาพหรือประกาศของทางการจะเปลี่ยน", "ปฏิบัติตามคำแนะนำของหน่วยงานท้องถิ่น"],
    },
}


def lang(locale: str) -> str:
    return "th" if locale.lower().startswith("th") else "en"


def fallback_explanation(
    *,
    action: ActionCode,
    risk_level: RiskLevel,
    reason_codes: list[ReasonCode],
    limitations: list[str],
    locale: str,
    delay_minutes: int | None,
) -> Explanation:
    lg = lang(locale)
    reasons = [REASON[lg][c] for c in reason_codes if c in REASON[lg]][:5]
    summary = SUMMARY[lg][action]
    if action == ActionCode.DELAY and delay_minutes:
        summary += (
            f" A delay of about {delay_minutes // 60} hours is suggested."
            if lg == "en"
            else f" แนะนำเลื่อนประมาณ {delay_minutes // 60} ชั่วโมง"
        )
    lim = [f"{'Risk level' if lg == 'en' else 'ระดับความเสี่ยง'}: {risk_level.value}"] + limitations[:5]
    return Explanation(
        action_code=action.value,
        short_summary=summary,
        reasons=reasons,
        immediate_actions=ACTIONS[lg][action],
        limitations=lim,
        citations_used=[],
    )
