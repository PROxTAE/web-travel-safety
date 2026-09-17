"use client";

import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type Toast = { id: number; title: string; description?: string; tone: "success" | "warning" | "danger" | "info" };
type ToastApi = { push: (t: Omit<Toast, "id">) => void };

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}

const ICONS = { success: CheckCircle2, warning: TriangleAlert, danger: TriangleAlert, info: Info };
const TONES = {
  success: "border-primary/40 bg-white",
  warning: "border-amber/50 bg-white",
  danger: "border-coral/50 bg-white",
  info: "border-weather/40 bg-white",
};
const ICON_TONES = { success: "text-primary bg-mint", warning: "text-amber bg-amber/10", danger: "text-coral bg-coral/10", info: "text-weather bg-weather/10" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev, { ...t, id }]);
    setTimeout(() => setItems((prev) => prev.filter((x) => x.id !== id)), 6000);
  }, []);
  const api = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-[min(92vw,22rem)]" role="status" aria-live="polite">
        {items.map((t) => {
          const Icon = ICONS[t.tone];
          return (
            <div key={t.id} className={`sta-card flex items-start gap-3 p-3 border ${TONES[t.tone]}`}>
              <span className={`sta-icon-tile !w-9 !h-9 ${ICON_TONES[t.tone]}`}>
                <Icon size={18} aria-hidden />
              </span>
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-navy">{t.title}</p>
                {t.description && <p className="text-sm text-ink-muted">{t.description}</p>}
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                className="text-ink-muted hover:text-navy"
                onClick={() => setItems((prev) => prev.filter((x) => x.id !== t.id))}
              >
                <X size={16} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
