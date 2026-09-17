"use client";

import { WifiOff } from "lucide-react";
import { useEffect, useState } from "react";

/** Global offline banner. Never covers emergency actions (rendered above main, not fixed). */
export function DataStatusBanner() {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const update = () => setOnline(typeof navigator === "undefined" ? true : navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  if (online) return null;
  return (
    <div role="alert" className="mx-4 lg:mx-6 mb-3 flex items-center gap-2 rounded-2xl border border-amber/50 bg-amber/10 px-4 py-2 text-navy">
      <WifiOff size={18} aria-hidden />
      <span className="font-semibold">You are offline.</span>
      <span className="text-sm text-ink-muted">Live safety data cannot refresh. Emergency numbers shown are the last verified values.</span>
    </div>
  );
}
