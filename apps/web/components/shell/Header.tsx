"use client";

import { ChevronDown, LogOut, Sun, UserRound } from "lucide-react";
import Image from "next/image";
import { signOut } from "next-auth/react";
import { useState } from "react";

import { useMe } from "@/features/auth/useMe";

export function Header({ userName }: { userName: string | null }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const name = me.data?.display_name ?? userName ?? "Traveler";
  return (
    <header className="flex items-center justify-between gap-4 px-4 lg:px-6 pt-4 pb-3">
      <div className="flex items-center gap-3">
        <Image src="/assets/branding/app-logo-mark.png" alt="" width={48} height={48} priority className="lg:hidden" />
        <div>
          <h1 className="text-2xl lg:text-[1.75rem] font-extrabold text-navy leading-tight">Smart Travel Assistant</h1>
          <p className="text-sm text-ink-muted hidden sm:block">Safer Trips · Smarter Decisions · Happier Journeys</p>
        </div>
      </div>
      <div className="relative flex items-center gap-3">
        <Sun className="text-amber hidden sm:block" aria-hidden />
        <div className="text-right hidden sm:block">
          <p className="font-bold text-navy leading-tight">Hello, {name}!</p>
          <p className="text-sm text-ink-muted">Explore the world safely</p>
        </div>
        <button
          type="button"
          className="flex items-center gap-1 rounded-full bg-white border border-line p-1 pr-2 shadow-card"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="sta-icon-tile !w-10 !h-10 rounded-full bg-mint text-primary-deep">
            <UserRound size={20} aria-hidden />
          </span>
          <ChevronDown size={16} aria-hidden />
          <span className="sr-only">Account menu</span>
        </button>
        {open && (
          <div role="menu" className="absolute right-0 top-14 sta-card p-2 min-w-44 z-30">
            <p className="px-3 py-2 text-sm text-ink-muted">
              {me.data?.locale ?? "en-US"} · {me.data?.timezone ?? "UTC"}
            </p>
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 hover:bg-mint text-navy"
              onClick={() => signOut({ callbackUrl: "/login" })}
            >
              <LogOut size={16} aria-hidden /> Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
