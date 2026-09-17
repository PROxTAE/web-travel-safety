"use client";

import { Bot, CalendarDays, Home, MapPin, ShieldAlert } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Header } from "@/components/shell/Header";
import { DataStatusBanner } from "@/components/ui/DataStatusBanner";
import { cn } from "@/lib/utils";

export const NAV = [
  { href: "/dashboard", label: "Overview", icon: Home },
  { href: "/trips/new", label: "My Trip", icon: CalendarDays, match: "/trips" },
  { href: "/safety-map", label: "Safety Map", icon: MapPin },
  { href: "/assistant/new", label: "Assistant", icon: Bot, match: "/assistant" },
  { href: "/emergency", label: "Emergency", icon: ShieldAlert },
] as const;

export function AppShell({ children, userName }: { children: ReactNode; userName: string | null }) {
  const pathname = usePathname();
  const isActive = (item: (typeof NAV)[number]) => pathname.startsWith("match" in item ? item.match : item.href);
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[11rem_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-white focus:p-2">
        Skip to content
      </a>
      {/* Desktop sidebar — fixed rail per screens 01–06 */}
      <aside className="sta-sidebar-bg hidden lg:flex flex-col border-r border-line relative overflow-hidden" aria-label="Primary">
        <div className="px-5 pt-5 pb-2">
          <Image src="/assets/branding/app-logo-mark.png" alt="" width={72} height={72} priority className="opacity-90" />
        </div>
        <nav className="px-3 mt-4 flex flex-col gap-1">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = isActive(item);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-2xl px-4 py-3 font-semibold transition-colors",
                  active ? "bg-primary-deep text-white shadow-card" : "text-navy hover:bg-white/70",
                )}
              >
                <Icon size={20} aria-hidden />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <p className="mt-auto px-5 pb-6 text-primary-deep italic font-semibold leading-tight" aria-hidden>
          Travel
          <br />
          Safer
          <br />
          Go Further 🍃
        </p>
        <Image
          src="/assets/illustrations/hero-global-travel-banner.png"
          alt=""
          width={600}
          height={200}
          className="absolute -bottom-6 -left-24 w-[22rem] opacity-70 pointer-events-none select-none"
        />
      </aside>

      <div className="flex flex-col min-h-screen">
        <Header userName={userName} />
        <DataStatusBanner />
        <main id="main" className="flex-1 px-4 pb-24 lg:px-6 lg:pb-8">
          {children}
        </main>
      </div>

      {/* Mobile bottom navigation */}
      <nav
        aria-label="Primary"
        className="lg:hidden fixed bottom-0 inset-x-0 z-40 bg-white/95 backdrop-blur border-t border-line grid grid-cols-5"
      >
        {NAV.map((item) => {
          const Icon = item.icon;
          const active = isActive(item);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn("flex flex-col items-center gap-0.5 py-2 text-[11px] font-semibold", active ? "text-primary" : "text-ink-muted")}
            >
              <Icon size={20} aria-hidden />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
