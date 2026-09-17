"use client";

import { Button } from "@heroui/react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Bus, CalendarDays, Car, Leaf, Plane, Search, ShieldCheck, TrainFront, Wallet } from "lucide-react";
import { useMemo } from "react";
import { Controller, useForm } from "react-hook-form";

import { LocationSearch } from "@/components/trip/LocationSearch";
import { TRAVEL_MODES, isoToLocal, localToIso, tripFormSchema, type TripFormValues } from "@/components/trip/tripSchema";
import type { TripInput } from "@/lib/api/endpoints";
import type { LocationRef, TravelMode, Trip } from "@/lib/api/generated/contracts";
import { cn } from "@/lib/utils";

const MODE_ICONS = { FLIGHT: Plane, TRAIN: TrainFront, BUS: Bus, CAR: Car } as const;

export function tripToForm(trip: Trip): TripFormValues {
  return {
    origin: trip.origin,
    destination: trip.destination,
    departureLocal: isoToLocal(trip.departure_time, trip.timezone),
    returnLocal: trip.return_time ? isoToLocal(trip.return_time, trip.timezone) : "",
    travel_modes: trip.travel_modes,
    prefer_safer_route: trip.preferences.prefer_safer_route ?? true,
    prefer_lower_emissions: trip.preferences.prefer_lower_emissions ?? false,
    prefer_lower_cost: trip.preferences.prefer_lower_cost ?? false,
    timezone: trip.timezone,
  };
}

export function formToInput(v: TripFormValues): TripInput {
  return {
    origin: v.origin as LocationRef,
    destination: v.destination as LocationRef,
    departure_time: localToIso(v.departureLocal, v.timezone) as string,
    return_time: v.returnLocal ? localToIso(v.returnLocal, v.timezone) : null,
    travel_modes: v.travel_modes as TravelMode[],
    preferences: {
      prefer_safer_route: v.prefer_safer_route,
      prefer_lower_emissions: v.prefer_lower_emissions,
      prefer_lower_cost: v.prefer_lower_cost,
    },
    timezone: v.timezone,
  };
}

export function TripForm({
  initial,
  onSubmit,
  submitting,
  onLocationsChange,
  locale,
  submitLabel = "Find safe routes",
}: {
  initial?: TripFormValues;
  onSubmit: (values: TripFormValues) => void;
  submitting: boolean;
  onLocationsChange?: (o: LocationRef | null, d: LocationRef | null) => void;
  locale?: string;
  submitLabel?: string;
}) {
  const defaults = useMemo<TripFormValues>(
    () =>
      initial ?? {
        origin: null,
        destination: null,
        departureLocal: "",
        returnLocal: "",
        travel_modes: ["FLIGHT"],
        prefer_safer_route: true,
        prefer_lower_emissions: false,
        prefer_lower_cost: false,
        timezone: "UTC",
      },
    [initial],
  );
  const form = useForm<TripFormValues>({ resolver: zodResolver(tripFormSchema), defaultValues: defaults, mode: "onSubmit" });
  const { control, handleSubmit, watch, setValue, formState } = form;
  const origin = watch("origin");
  const destination = watch("destination");
  const modes = watch("travel_modes");
  const tz = watch("timezone");

  const setLocation = (field: "origin" | "destination", loc: LocationRef | null) => {
    setValue(field, loc, { shouldValidate: formState.isSubmitted });
    if (field === "origin" && loc) setValue("timezone", loc.timezone);
    onLocationsChange?.(field === "origin" ? loc : origin, field === "destination" ? loc : destination);
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate aria-busy={submitting}>
      <h2 className="flex items-center gap-2 text-lg font-extrabold text-navy">
        <CalendarDays className="text-primary-deep" aria-hidden /> Trip Details
      </h2>
      <LocationSearch label="From" value={origin} onChange={(l) => setLocation("origin", l)} locale={locale} error={formState.errors.origin?.message} />
      <LocationSearch label="To" value={destination} onChange={(l) => setLocation("destination", l)} locale={locale} error={formState.errors.destination?.message} />

      <Controller
        control={control}
        name="departureLocal"
        render={({ field, fieldState }) => (
          <div>
            <label htmlFor="departure" className="block text-sm font-bold text-navy mb-1">
              Departure <span className="font-normal text-ink-muted">({tz})</span>
            </label>
            <input id="departure" type="datetime-local" className="w-full rounded-2xl border border-line bg-white px-3 py-2.5 text-navy" aria-invalid={Boolean(fieldState.error)} {...field} />
            {fieldState.error && <p className="mt-1 text-xs text-coral">{fieldState.error.message}</p>}
          </div>
        )}
      />
      <Controller
        control={control}
        name="returnLocal"
        render={({ field, fieldState }) => (
          <div>
            <label htmlFor="return" className="block text-sm font-bold text-navy mb-1">
              Return <span className="font-normal text-ink-muted">(optional)</span>
            </label>
            <input id="return" type="datetime-local" className="w-full rounded-2xl border border-line bg-white px-3 py-2.5 text-navy" aria-invalid={Boolean(fieldState.error)} {...field} />
            {fieldState.error && <p className="mt-1 text-xs text-coral">{fieldState.error.message}</p>}
          </div>
        )}
      />

      <fieldset>
        <legend className="text-sm font-bold text-navy mb-1">Travel mode</legend>
        <div className="grid grid-cols-4 gap-2">
          {TRAVEL_MODES.map((m) => {
            const Icon = MODE_ICONS[m as keyof typeof MODE_ICONS];
            const on = modes.includes(m);
            return (
              <button
                key={m}
                type="button"
                aria-pressed={on}
                onClick={() => setValue("travel_modes", on ? modes.filter((x) => x !== m) : [...modes, m], { shouldValidate: formState.isSubmitted })}
                className={cn(
                  "flex items-center justify-center gap-1.5 rounded-2xl border px-2 py-2.5 text-sm font-semibold",
                  on ? "border-primary bg-mint text-primary-deep" : "border-line bg-white text-navy",
                )}
              >
                <Icon size={16} aria-hidden />
                {m.charAt(0) + m.slice(1).toLowerCase()}
              </button>
            );
          })}
        </div>
        {formState.errors.travel_modes && <p className="mt-1 text-xs text-coral">{formState.errors.travel_modes.message}</p>}
      </fieldset>

      <fieldset>
        <legend className="text-sm font-bold text-navy mb-1">Preferences</legend>
        <div className="grid grid-cols-3 gap-2">
          {(
            [
              ["prefer_safer_route", "Safer route", ShieldCheck],
              ["prefer_lower_emissions", "Eco-friendly", Leaf],
              ["prefer_lower_cost", "Lower cost", Wallet],
            ] as const
          ).map(([name, label, Icon]) => {
            const on = watch(name);
            return (
              <button
                key={name}
                type="button"
                aria-pressed={on}
                onClick={() => setValue(name, !on)}
                className={cn(
                  "flex items-center justify-center gap-1.5 rounded-2xl border px-2 py-2 text-xs font-semibold",
                  on ? "border-primary bg-mint text-primary-deep" : "border-line bg-white text-navy",
                )}
              >
                <Icon size={14} aria-hidden />
                {label}
              </button>
            );
          })}
        </div>
      </fieldset>

      <Button type="submit" size="lg" fullWidth isDisabled={submitting}>
        <Search size={18} aria-hidden /> {submitting ? "Checking…" : submitLabel}
      </Button>
    </form>
  );
}
