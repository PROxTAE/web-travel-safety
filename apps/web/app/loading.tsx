import { Skeleton } from "@heroui/react";

export default function Loading() {
  return (
    <div className="p-6 grid gap-4" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-10 w-1/3 rounded-xl" />
      <Skeleton className="h-40 w-full rounded-2xl" />
      <Skeleton className="h-40 w-full rounded-2xl" />
    </div>
  );
}
