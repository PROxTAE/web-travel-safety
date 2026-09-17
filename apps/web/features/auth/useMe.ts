"use client";

import { useQuery } from "@tanstack/react-query";

import { endpoints } from "@/lib/api/endpoints";
import { queryKeys } from "@/lib/query";

export function useMe() {
  return useQuery({ queryKey: queryKeys.me, queryFn: async () => (await endpoints.me()).data, staleTime: 5 * 60_000 });
}
