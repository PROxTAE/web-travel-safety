"use client";

import { use } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { TripPlanner } from "@/components/trip/TripPlanner";
import { ErrorState } from "@/components/ui/primitives";
import { useTrip } from "@/features/trips/hooks";

export default function TripPage({ params }: { params: Promise<{ tripId: string }> }) {
  const { tripId } = use(params);
  const trip = useTrip(tripId);
  if (trip.isError) return <ErrorState error={trip.error} onRetry={() => trip.refetch()} />;
  if (!trip.data) return <div className="h-96 animate-pulse rounded-2xl bg-white/60" aria-busy="true" />;
  return (
    <>
      <PageHeader title="My Trip" subtitle={`${trip.data.origin.display_name} → ${trip.data.destination.display_name} · revision ${trip.data.revision}`} />
      <TripPlanner key={`${trip.data.id}:${trip.data.revision}`} trip={trip.data} />
    </>
  );
}
