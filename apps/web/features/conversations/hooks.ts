"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { endpoints } from "@/lib/api/endpoints";
import type { Intent } from "@/lib/api/generated/contracts";
import { queryKeys } from "@/lib/query";

export function useConversations() {
  return useQuery({ queryKey: queryKeys.conversations, queryFn: async () => (await endpoints.conversations()).data });
}

export function useMessages(conversationId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.messages(conversationId ?? ""),
    queryFn: async () => (await endpoints.messages(conversationId as string)).data,
    enabled: Boolean(conversationId) && conversationId !== "new",
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { trip_id?: string | null; title?: string }) =>
      (await endpoints.createConversation(args.trip_id, args.title)).data,
    onSuccess: () => void qc.invalidateQueries({ queryKey: queryKeys.conversations }),
  });
}

export function usePostMessage(conversationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { text: string; intent_hint?: Intent }) =>
      (await endpoints.postMessage(conversationId, args.text, args.intent_hint)).data,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.messages(conversationId) });
      void qc.invalidateQueries({ queryKey: queryKeys.conversations });
    },
  });
}
