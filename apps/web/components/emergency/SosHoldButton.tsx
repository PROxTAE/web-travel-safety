"use client";

import { Phone } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

export const HOLD_MS = 3000;

/**
 * Hold-to-activate SOS. Pointer or keyboard (Space/Enter) must be held for HOLD_MS; releasing early cancels.
 * Reports progress for the ring and calls onActivate exactly once. No network call happens here.
 */
export function SosHoldButton({ onActivate, disabled }: { onActivate: () => void; disabled?: boolean }) {
  const [progress, setProgress] = useState(0);
  const holdStart = useRef<number | null>(null);
  const raf = useRef<number | null>(null);
  const fired = useRef(false);

  const stop = useCallback(() => {
    holdStart.current = null;
    if (raf.current) cancelAnimationFrame(raf.current);
    raf.current = null;
    setProgress(0);
  }, []);

  const onActivateRef = useRef(onActivate);
  useEffect(() => {
    onActivateRef.current = onActivate;
  }, [onActivate]);

  const start = useCallback(() => {
    if (disabled || holdStart.current !== null) return;
    fired.current = false;
    holdStart.current = performance.now();
    const tick = () => {
      if (holdStart.current === null) return;
      const pct = Math.min(100, ((performance.now() - holdStart.current) / HOLD_MS) * 100);
      setProgress(pct);
      if (pct >= 100) {
        if (!fired.current) {
          fired.current = true;
          onActivateRef.current();
        }
        stop();
        return;
      }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [disabled, stop]);

  useEffect(() => stop, [stop]);

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="sta-hold-ring rounded-full p-3" style={{ ["--progress" as string]: progress }}>
        <button
          type="button"
          aria-label="Hold for SOS. Press and hold for 3 seconds."
          aria-describedby="sos-help"
          disabled={disabled}
          onPointerDown={(e) => {
            e.preventDefault();
            (e.currentTarget as HTMLButtonElement).setPointerCapture?.(e.pointerId);
            start();
          }}
          onPointerUp={stop}
          onPointerCancel={stop}
          onPointerLeave={stop}
          onKeyDown={(e) => {
            if ((e.key === " " || e.key === "Enter") && !e.repeat) {
              e.preventDefault();
              start();
            }
          }}
          onKeyUp={(e) => {
            if (e.key === " " || e.key === "Enter") stop();
          }}
          onBlur={stop}
          className="flex h-56 w-56 flex-col items-center justify-center rounded-full bg-coral text-white shadow-[0_20px_50px_-20px_rgba(242,78,84,0.8)] outline-none ring-offset-4 focus-visible:ring-4 focus-visible:ring-coral/60 active:scale-[0.98] disabled:opacity-50 select-none touch-none"
        >
          <Phone size={30} aria-hidden />
          <span className="mt-2 text-lg font-bold leading-tight">Hold for</span>
          <span className="text-4xl font-extrabold leading-tight">SOS</span>
          <span className="mt-1 text-sm">Press and hold for 3 seconds</span>
        </button>
      </div>
      <p id="sos-help" className="text-xs text-ink-muted" aria-live="polite">
        {progress > 0 ? `Keep holding… ${Math.round(progress)}%` : "Nothing is sent until you confirm on the next step."}
      </p>
    </div>
  );
}
