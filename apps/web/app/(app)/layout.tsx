import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/AppShell";
import { auth } from "@/lib/auth";

export default async function AppLayout({ children }: { children: ReactNode }) {
  const session = await auth();
  return <AppShell userName={session?.user?.name ?? null}>{children}</AppShell>;
}
