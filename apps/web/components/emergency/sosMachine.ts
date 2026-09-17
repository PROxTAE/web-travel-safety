/** SOS state machine: Hold -> Confirm -> Share location (optional, consented) -> Connect help. Pure + unit-tested. */
export type SosStep = "HOLD" | "CONFIRM" | "SHARE" | "CONNECT";
export type SosState = { step: SosStep; confirmed: boolean; sharing: boolean; startedAt: number | null };
export type SosEvent =
  | { type: "HOLD_COMPLETE"; at: number }
  | { type: "CONFIRM" }
  | { type: "CANCEL" }
  | { type: "SHARE_ON" }
  | { type: "SHARE_OFF" }
  | { type: "SKIP_SHARE" }
  | { type: "CONNECT" }
  | { type: "RESET" };

export const SOS_INITIAL: SosState = { step: "HOLD", confirmed: false, sharing: false, startedAt: null };
export const SOS_STEPS: Array<{ key: SosStep; label: string }> = [
  { key: "HOLD", label: "Hold" },
  { key: "CONFIRM", label: "Confirm" },
  { key: "SHARE", label: "Share location" },
  { key: "CONNECT", label: "Connect help" },
];

export function sosReducer(s: SosState, e: SosEvent): SosState {
  switch (e.type) {
    case "HOLD_COMPLETE":
      return s.step === "HOLD" ? { ...s, step: "CONFIRM", startedAt: e.at } : s;
    case "CONFIRM":
      return s.step === "CONFIRM" ? { ...s, confirmed: true, step: "SHARE" } : s;
    case "CANCEL":
    case "RESET":
      return SOS_INITIAL;
    case "SHARE_ON":
      return s.confirmed && (s.step === "SHARE" || s.step === "CONNECT") ? { ...s, sharing: true, step: "CONNECT" } : s;
    case "SHARE_OFF":
      return { ...s, sharing: false };
    case "SKIP_SHARE":
      return s.step === "SHARE" ? { ...s, step: "CONNECT" } : s;
    case "CONNECT":
      return s.confirmed ? { ...s, step: "CONNECT" } : s;
  }
}
