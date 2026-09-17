"use client";

import { Button } from "@heroui/react";
import { useEffect } from "react";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // never log user data; the digest is enough to correlate with server logs
    console.error("page_error", error.digest ?? error.name);
  }, [error]);
  return (
    <main className="min-h-screen grid place-items-center p-6">
      <div className="sta-card p-8 max-w-md text-center">
        <h1 className="text-2xl font-bold text-navy">Something went wrong</h1>
        <p className="text-ink-muted mt-2">We could not load this page. Your trip data is safe.</p>
        {error.digest && <p className="text-xs text-ink-muted mt-2">Reference: {error.digest}</p>}
        <Button className="mt-6" onPress={reset}>
          Try again
        </Button>
      </div>
    </main>
  );
}
