import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { Providers } from "@/app/providers";

import "@/styles/globals.css";

export const metadata: Metadata = {
  title: { default: "Smart Travel Assistant", template: "%s · Smart Travel Assistant" },
  description: "Safer trips, smarter decisions, happier journeys.",
  icons: { icon: "/assets/branding/app-logo-mark.png" },
};

export const viewport: Viewport = { themeColor: "#08B88A", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="sta-shell-bg min-h-screen">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
