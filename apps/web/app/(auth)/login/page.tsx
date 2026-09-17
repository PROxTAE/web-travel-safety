import Image from "next/image";
import { redirect } from "next/navigation";

import { auth, signIn } from "@/lib/auth";

export const metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const session = await auth();
  const params = await searchParams;
  const next = params.next && params.next.startsWith("/") && !params.next.startsWith("//") ? params.next : "/dashboard";
  if (session && !session.error) redirect(next);

  async function start() {
    "use server";
    await signIn("keycloak", { redirectTo: next });
  }

  return (
    <main className="min-h-screen grid lg:grid-cols-2">
      <section className="flex flex-col justify-center p-8 lg:p-16">
        <Image src="/assets/branding/logo-horizontal.png" alt="Smart Travel Assistant" width={320} height={90} priority />
        <h1 className="mt-10 text-4xl font-extrabold text-navy">Safer Trips · Smarter Decisions</h1>
        <p className="mt-3 text-ink-muted max-w-md">
          Live weather, transport and hazard data combined into one clear recommendation for every journey. Sign in with your
          traveler account to continue.
        </p>
        {params.error && (
          <p role="alert" className="mt-4 rounded-xl border border-coral/40 bg-coral/5 p-3 text-sm text-navy">
            Sign-in did not complete ({params.error}). Please try again.
          </p>
        )}
        {session?.error === "RefreshTokenError" && (
          <p role="alert" className="mt-4 rounded-xl border border-amber/50 bg-amber/10 p-3 text-sm text-navy">
            Your session expired. Please sign in again.
          </p>
        )}
        <form action={start} className="mt-8">
          <button
            type="submit"
            className="rounded-2xl bg-primary px-6 py-3 text-lg font-bold text-white shadow-card hover:bg-primary-deep focus-visible:outline-primary-deep"
          >
            Sign in with Keycloak
          </button>
        </form>
        <p className="mt-6 text-xs text-ink-muted">
          We only store your trips, consents and, if you choose, an encrypted emergency profile. Location is used only when you
          allow it.
        </p>
      </section>
      <aside className="hidden lg:flex items-end justify-center bg-mint relative overflow-hidden">
        <Image src="/assets/illustrations/hero-global-travel-banner.png" alt="" fill className="object-cover opacity-90" />
        <Image src="/assets/mascot/mascot-welcome.png" alt="" width={420} height={420} className="relative z-10 mb-6 drop-shadow-xl" />
      </aside>
    </main>
  );
}
