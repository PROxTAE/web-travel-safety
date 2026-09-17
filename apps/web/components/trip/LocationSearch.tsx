"use client";

import { MapPin, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { endpoints } from "@/lib/api/endpoints";
import type { LocationRef } from "@/lib/api/generated/contracts";

const MIN_QUERY = 2;
const DEBOUNCE_MS = 350;

/**
 * Debounced geocoding autocomplete (real provider through the API). The previous request is aborted when the
 * query changes. A chosen place is returned with confirmed_by_user=false; the parent must confirm it on the map.
 */
export function LocationSearch({
  label,
  value,
  onChange,
  locale = "en",
  error,
}: {
  label: string;
  value: LocationRef | null;
  onChange: (loc: LocationRef | null) => void;
  locale?: string;
  error?: string | undefined;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LocationRef[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [status, setStatus] = useState<"idle" | "loading" | "error" | "empty">("idle");
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (value) return;
    const q = query.trim();
    if (q.length < MIN_QUERY) return;
    const t = setTimeout(async () => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setStatus("loading");
      try {
        const res = await endpoints.searchLocations(q, locale, ctrl.signal);
        if (ctrl.signal.aborted) return;
        setResults(res.data);
        setStatus(res.data.length ? "idle" : "empty");
        setOpen(true);
        setActive(res.data.length ? 0 : -1);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setStatus("error");
        setErrMsg(e instanceof ApiError ? e.message : "Search failed");
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query, locale, value]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const choose = (loc: LocationRef) => {
    onChange({ ...loc, confirmed_by_user: false });
    setOpen(false);
    setQuery("");
  };

  return (
    <div className="relative">
      <label htmlFor={id} className="block text-sm font-bold text-navy mb-1">
        {label}
      </label>
      {value ? (
        <div className="flex items-center gap-2 rounded-2xl border border-line bg-white px-3 py-2.5">
          <MapPin size={18} className="text-primary" aria-hidden />
          <span className="flex-1 truncate text-navy">{value.display_name}</span>
          {value.confirmed_by_user ? (
            <span className="text-xs font-semibold text-primary-deep">Confirmed</span>
          ) : (
            <span className="text-xs font-semibold text-[#b45f00]">Confirm on map</span>
          )}
          <button type="button" aria-label={`Clear ${label}`} onClick={() => onChange(null)} className="rounded-full p-1 hover:bg-mint">
            <X size={16} aria-hidden />
          </button>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2 rounded-2xl border border-line bg-white px-3 py-2.5 focus-within:border-primary">
            <MapPin size={18} className="text-primary" aria-hidden />
            <input
              id={id}
              role="combobox"
              aria-expanded={open}
              aria-controls={`${id}-list`}
              aria-autocomplete="list"
              aria-activedescendant={active >= 0 ? `${id}-opt-${active}` : undefined}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? `${id}-err` : undefined}
              className="flex-1 bg-transparent outline-none text-navy"
              placeholder="Search a city or place"
              autoComplete="off"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (e.target.value.trim().length < MIN_QUERY) {
                  setResults([]);
                  setStatus("idle");
                  setOpen(false);
                }
              }}
              onFocus={() => results.length && setOpen(true)}
              onBlur={() => setTimeout(() => setOpen(false), 120)}
              onKeyDown={(e) => {
                if (!open) return;
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(a + 1, results.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) => Math.max(a - 1, 0));
                } else if (e.key === "Enter" && active >= 0 && results[active]) {
                  e.preventDefault();
                  choose(results[active]);
                } else if (e.key === "Escape") setOpen(false);
              }}
            />
          </div>
          {status === "loading" && <p className="mt-1 text-xs text-ink-muted">Searching…</p>}
          {status === "empty" && <p className="mt-1 text-xs text-ink-muted">No places found. Try a different spelling.</p>}
          {status === "error" && <p className="mt-1 text-xs text-coral">{errMsg}</p>}
          {open && results.length > 0 && (
            <ul id={`${id}-list`} role="listbox" className="absolute z-20 mt-1 w-full sta-card max-h-64 overflow-auto p-1">
              {results.map((r, i) => (
                <li
                  key={r.place_id}
                  id={`${id}-opt-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className={`cursor-pointer rounded-xl px-3 py-2 text-sm ${i === active ? "bg-mint text-navy" : "text-navy hover:bg-surface-secondary"}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(r)}
                >
                  <span className="font-semibold">{r.display_name}</span>
                  <span className="block text-xs text-ink-muted">
                    {r.admin1 ? `${r.admin1}, ` : ""}
                    {r.country_code} · {r.timezone}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {error && (
        <p id={`${id}-err`} className="mt-1 text-xs text-coral">
          {error}
        </p>
      )}
    </div>
  );
}
