"use client";

import { Button } from "@heroui/react";
import { ChevronRight, FileText, ShieldCheck, UserRound, UsersRound } from "lucide-react";
import { useState } from "react";

import { useToast } from "@/components/ui/toast";
import { useConsents, useEmergencyProfile, useGrantConsent, useSaveEmergencyProfile } from "@/features/emergency/hooks";
import { ApiError } from "@/lib/api/client";
import type { EmergencyProfile } from "@/lib/api/generated/contracts";

const EMPTY: Omit<EmergencyProfile, "updated_at"> = {
  blood_type: null,
  allergies: [],
  medications: [],
  medical_notes: null,
  contacts: [],
  insurance_provider: null,
  insurance_policy_ref: null,
  insurance_phone: null,
};

/** Medical / contacts / insurance stored encrypted server-side; never sent to analytics or logs. */
export function EmergencyProfilePanel() {
  const profile = useEmergencyProfile();
  const consents = useConsents();
  const grant = useGrantConsent();
  const save = useSaveEmergencyProfile();
  const toast = useToast();
  const [editing, setEditing] = useState<"medical" | "contacts" | "insurance" | "review" | null>(null);
  const [draft, setDraft] = useState<Omit<EmergencyProfile, "updated_at"> | null>(null);
  const hasConsent = consents.data?.some((c) => c.type === "EMERGENCY_PROFILE" && c.granted && !c.revoked_at) ?? false;
  const current = draft ?? { ...EMPTY, ...(profile.data ?? {}) };

  const open = (section: typeof editing) => {
    setDraft({ ...EMPTY, ...(profile.data ?? {}) });
    setEditing(section);
  };
  const persist = async () => {
    if (!draft) return;
    try {
      if (!hasConsent) await grant.mutateAsync({ type: "EMERGENCY_PROFILE", granted: true });
      await save.mutateAsync(draft);
      toast.push({ tone: "success", title: "Emergency profile saved", description: "Stored encrypted. Only you can read it." });
      setEditing(null);
      setDraft(null);
    } catch (e) {
      toast.push({ tone: "danger", title: "Could not save", description: e instanceof ApiError ? e.message : undefined });
    }
  };

  const contactsText = current.contacts?.map((c) => `${c.name}${c.relationship ? ` (${c.relationship})` : ""}: ${c.phone}`).join("\n") ?? "";

  return (
    <section className="sta-card p-4" aria-labelledby="emergency-profile">
      <div className="flex items-center gap-3">
        <UserRound className="text-primary-deep" aria-hidden />
        <div>
          <h2 id="emergency-profile" className="font-extrabold text-navy">Emergency profile</h2>
          <p className="text-xs text-ink-muted">Keep your information ready in case you need help.</p>
        </div>
      </div>
      <ul className="mt-3 flex flex-col gap-2">
        {(
          [
            ["medical", "Medical notes", FileText],
            ["contacts", "Emergency contacts", UsersRound],
            ["insurance", "Travel insurance", ShieldCheck],
          ] as const
        ).map(([key, label, Icon]) => (
          <li key={key}>
            <button type="button" onClick={() => open(key)} className="flex w-full items-center gap-3 rounded-2xl bg-surface-secondary px-3 py-2 text-left hover:bg-mint">
              <span className="sta-icon-tile !w-8 !h-8 bg-white text-primary-deep"><Icon size={16} aria-hidden /></span>
              <span className="flex-1 font-semibold text-navy">{label}</span>
              <ChevronRight className="text-ink-muted" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
      <Button variant="secondary" fullWidth className="mt-3" onPress={() => open("review")}>
        Review details <ChevronRight size={16} aria-hidden />
      </Button>
      {profile.data?.updated_at && <p className="mt-2 text-xs text-ink-muted">Last updated {new Date(profile.data.updated_at).toLocaleString()}</p>}

      {editing && draft && (
        <div role="dialog" aria-modal="true" aria-label="Edit emergency profile" className="fixed inset-0 z-40 grid place-items-center bg-navy/30 p-4">
          <form
            className="sta-card w-full max-w-lg p-5 flex flex-col gap-3 max-h-[90vh] overflow-y-auto"
            onSubmit={(e) => {
              e.preventDefault();
              void persist();
            }}
          >
            <h3 className="text-lg font-extrabold text-navy">{editing === "review" ? "Review details" : "Edit emergency profile"}</h3>
            {!hasConsent && (
              <p className="rounded-2xl bg-amber/10 p-3 text-sm text-navy">Saving stores this data encrypted with your consent (EMERGENCY_PROFILE). You can delete it any time.</p>
            )}
            {(editing === "medical" || editing === "review") && (
              <>
                <Field label="Blood type" value={draft.blood_type ?? ""} onChange={(v) => setDraft({ ...draft, blood_type: v || null })} />
                <Field label="Allergies (comma separated)" value={draft.allergies?.join(", ") ?? ""} onChange={(v) => setDraft({ ...draft, allergies: v.split(",").map((x) => x.trim()).filter(Boolean) })} />
                <Field label="Medications (comma separated)" value={draft.medications?.join(", ") ?? ""} onChange={(v) => setDraft({ ...draft, medications: v.split(",").map((x) => x.trim()).filter(Boolean) })} />
                <Field label="Medical notes" value={draft.medical_notes ?? ""} onChange={(v) => setDraft({ ...draft, medical_notes: v || null })} multiline />
              </>
            )}
            {(editing === "contacts" || editing === "review") && (
              <Field
                label="Emergency contacts (one per line: Name (relationship): +phone)"
                value={contactsText}
                multiline
                onChange={(v) =>
                  setDraft({
                    ...draft,
                    contacts: v
                      .split("\n")
                      .map((line) => line.trim())
                      .filter(Boolean)
                      .slice(0, 5)
                      .map((line) => {
                        const m = /^(.*?)(?:\s*\((.*?)\))?\s*:\s*(.+)$/.exec(line);
                        return m ? { name: m[1]!.trim(), relationship: m[2]?.trim() || null, phone: m[3]!.trim() } : { name: line, relationship: null, phone: "" };
                      })
                      .filter((c) => c.phone),
                  })
                }
              />
            )}
            {(editing === "insurance" || editing === "review") && (
              <>
                <Field label="Insurance provider" value={draft.insurance_provider ?? ""} onChange={(v) => setDraft({ ...draft, insurance_provider: v || null })} />
                <Field label="Policy reference" value={draft.insurance_policy_ref ?? ""} onChange={(v) => setDraft({ ...draft, insurance_policy_ref: v || null })} />
                <Field label="Insurance phone" value={draft.insurance_phone ?? ""} onChange={(v) => setDraft({ ...draft, insurance_phone: v || null })} />
              </>
            )}
            <div className="mt-2 flex justify-end gap-2">
              <Button variant="ghost" onPress={() => { setEditing(null); setDraft(null); }}>Cancel</Button>
              <Button type="submit" isDisabled={save.isPending || grant.isPending}>{save.isPending ? "Saving…" : "Save encrypted"}</Button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}

function Field({ label, value, onChange, multiline }: { label: string; value: string; onChange: (v: string) => void; multiline?: boolean }) {
  const id = label.replace(/\W+/g, "-").toLowerCase();
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-bold text-navy mb-1">{label}</label>
      {multiline ? (
        <textarea id={id} value={value} onChange={(e) => onChange(e.target.value)} rows={3} maxLength={2000} className="w-full rounded-2xl border border-line px-3 py-2 text-navy" autoComplete="off" />
      ) : (
        <input id={id} value={value} onChange={(e) => onChange(e.target.value)} maxLength={120} className="w-full rounded-2xl border border-line px-3 py-2 text-navy" autoComplete="off" />
      )}
    </div>
  );
}
