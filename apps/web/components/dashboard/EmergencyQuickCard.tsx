"use client";

import { ChevronRight, Phone, Plus, Siren } from "lucide-react";
import Link from "next/link";

import { DataFreshness } from "@/components/ui/primitives";
import { useEmergencyContacts } from "@/features/emergency/hooks";

/** Numbers are resolved from the verified directory for the trip's country — never hard-coded. */
export function EmergencyQuickCard({ countryCode }: { countryCode: string | null }) {
  const q = useEmergencyContacts(countryCode);
  const police = q.data?.contacts.find((c) => c.service_type === "POLICE" || c.service_type === "GENERAL_EMERGENCY");
  const medical = q.data?.contacts.find((c) => c.service_type === "MEDICAL");
  return (
    <section className="sta-card p-4" aria-labelledby="emergency-quick">
      <div className="flex items-center justify-between">
        <h2 id="emergency-quick" className="flex items-center gap-2 text-lg font-extrabold text-navy">
          <Siren className="text-coral" aria-hidden /> Emergency Help
        </h2>
        <span className="text-xs text-ink-muted">Stay safe, we&apos;re here for you</span>
      </div>
      <div className="mt-3 grid grid-cols-[1fr_1.4fr] gap-3">
        <Link
          href="/emergency"
          className="flex flex-col items-center justify-center rounded-2xl bg-coral text-white font-extrabold text-2xl py-6 shadow-card hover:bg-[#d93f45]"
        >
          <Phone aria-hidden />
          SOS
        </Link>
        <div className="flex flex-col gap-2">
          <ContactRow icon={<Phone size={16} aria-hidden />} contact={police} fallback="Police" loading={q.isLoading} />
          <ContactRow icon={<Plus size={16} aria-hidden />} contact={medical} fallback="Medical" loading={q.isLoading} />
        </div>
      </div>
      {q.data?.limitations?.length ? <p className="mt-2 text-xs text-[#b45f00]">{q.data.limitations.join(" · ")}</p> : null}
      {q.data?.contacts[0] && (
        <p className="mt-2 text-xs text-ink-muted">
          Directory {q.data.directory_version} · <DataFreshness fetchedAt={q.data.contacts[0].verified_at} label="verified" maxAgeMinutes={60 * 24 * 90} />
        </p>
      )}
      {!countryCode && <p className="mt-2 text-xs text-ink-muted">Plan a trip to load the local emergency numbers.</p>}
    </section>
  );
}

function ContactRow({
  icon,
  contact,
  fallback,
  loading,
}: {
  icon: React.ReactNode;
  contact: { phone: string; label: string } | undefined;
  fallback: string;
  loading: boolean;
}) {
  const inner = (
    <>
      <span className="sta-icon-tile !w-9 !h-9 bg-mint text-primary-deep">{icon}</span>
      <span className="flex-1">
        <span className="block text-lg font-extrabold text-navy">{loading ? "…" : (contact?.phone ?? "—")}</span>
        <span className="block text-xs text-ink-muted">{contact?.label ?? fallback}</span>
      </span>
      <ChevronRight className="text-ink-muted" aria-hidden />
    </>
  );
  // tel: opens the dialer only after an explicit tap; never auto-calls
  return contact ? (
    <a href={`tel:${contact.phone}`} className="flex items-center gap-3 rounded-2xl bg-surface-secondary px-3 py-2 hover:bg-mint">
      {inner}
    </a>
  ) : (
    <div className="flex items-center gap-3 rounded-2xl bg-surface-secondary px-3 py-2 opacity-70">{inner}</div>
  );
}
