import { z } from "zod";

import type { TravelMode } from "@/lib/api/generated/contracts";

export const TRAVEL_MODES: TravelMode[] = ["FLIGHT", "TRAIN", "BUS", "CAR"];

const locationSchema = z.object({
  place_id: z.string().min(1),
  display_name: z.string().min(1),
  coordinates: z.object({ type: z.literal("Point").optional(), coordinates: z.array(z.number()).min(2) }),
  country_code: z.string().length(2),
  admin1: z.string().nullable().optional(),
  timezone: z.string().min(1),
  provider: z.string().min(1),
  confirmed_by_user: z.boolean().optional(),
});

export const tripFormSchema = z
  .object({
    origin: locationSchema.nullable(),
    destination: locationSchema.nullable(),
    departureLocal: z.string().min(1, "Choose a departure date and time"),
    returnLocal: z.string().optional(),
    travel_modes: z.array(z.enum(["FLIGHT", "TRAIN", "BUS", "CAR", "WALK", "BICYCLE", "MULTIMODAL"])).min(1, "Pick at least one travel mode"),
    prefer_safer_route: z.boolean(),
    prefer_lower_emissions: z.boolean(),
    prefer_lower_cost: z.boolean(),
    timezone: z.string().min(1),
  })
  .superRefine((v, ctx) => {
    if (!v.origin) ctx.addIssue({ code: "custom", path: ["origin"], message: "Choose a departure place" });
    if (!v.destination) ctx.addIssue({ code: "custom", path: ["destination"], message: "Choose a destination" });
    if (v.origin && !v.origin.confirmed_by_user) ctx.addIssue({ code: "custom", path: ["origin"], message: "Confirm the departure pin on the map" });
    if (v.destination && !v.destination.confirmed_by_user)
      ctx.addIssue({ code: "custom", path: ["destination"], message: "Confirm the destination pin on the map" });
    if (v.origin && v.destination && v.origin.place_id === v.destination.place_id)
      ctx.addIssue({ code: "custom", path: ["destination"], message: "Destination must differ from the origin" });
    const dep = localToIso(v.departureLocal, v.timezone);
    if (!dep) ctx.addIssue({ code: "custom", path: ["departureLocal"], message: "Invalid departure time" });
    else if (new Date(dep).getTime() < Date.now() - 30 * 60_000)
      ctx.addIssue({ code: "custom", path: ["departureLocal"], message: "Departure is in the past (trip timezone)" });
    if (v.returnLocal) {
      const ret = localToIso(v.returnLocal, v.timezone);
      if (!ret) ctx.addIssue({ code: "custom", path: ["returnLocal"], message: "Invalid return time" });
      else if (dep && new Date(ret) <= new Date(dep)) ctx.addIssue({ code: "custom", path: ["returnLocal"], message: "Return must be after departure" });
    }
  });

export type TripFormValues = z.infer<typeof tripFormSchema>;

/**
 * Converts a datetime-local string interpreted in the trip's IANA timezone to an ISO instant.
 * Browser timezone is deliberately not used (plan §trips: validate in the trip timezone).
 */
export function localToIso(local: string, timeZone: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local);
  if (!m) return null;
  const [y, mo, d, h, mi] = [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4]), Number(m[5])];
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  try {
    const offset = tzOffsetMinutes(new Date(guess), timeZone);
    const utc = guess - offset * 60_000;
    // second pass handles DST edges where the offset changes across the guess
    const offset2 = tzOffsetMinutes(new Date(utc), timeZone);
    return new Date(guess - offset2 * 60_000).toISOString();
  } catch {
    return null;
  }
}

export function tzOffsetMinutes(date: Date, timeZone: string): number {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const parts = Object.fromEntries(dtf.formatToParts(date).map((p) => [p.type, p.value]));
  const asUtc = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute), Number(parts.second));
  return Math.round((asUtc - date.getTime()) / 60_000);
}

export function isoToLocal(iso: string, timeZone: string): string {
  const d = new Date(iso);
  const offset = tzOffsetMinutes(d, timeZone);
  return new Date(d.getTime() + offset * 60_000).toISOString().slice(0, 16);
}
