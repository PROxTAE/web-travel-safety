import { TripPlanner } from "@/components/trip/TripPlanner";
import { PageHeader } from "@/components/shell/PageHeader";

export const metadata = { title: "Plan a New Trip" };

export default function NewTripPage() {
  return (
    <>
      <PageHeader title="Plan a New Trip" subtitle="Find the safest and smartest way to your destination" />
      <TripPlanner trip={null} />
    </>
  );
}
