"use client";

import { useMutation } from "@tanstack/react-query";

import { endpoints } from "@/lib/api/endpoints";
import type { DeliveryChannel, FeedbackCategory, Severity } from "@/lib/api/generated/contracts";

export function useSendFeedback() {
  return useMutation({
    mutationFn: async (args: { recommendation_id: string; category: FeedbackCategory; text?: string }) =>
      (await endpoints.feedback(args.recommendation_id, args.category, args.text)).data,
  });
}

export function useSubscribe() {
  return useMutation({
    mutationFn: async (args: {
      trip_id: string;
      consent_id: string;
      channel?: DeliveryChannel;
      severity_threshold?: Severity;
    }) => (await endpoints.subscribe({ channel: "IN_APP", ...args })).data,
  });
}

export function useUnsubscribe() {
  return useMutation({ mutationFn: async (id: string) => (await endpoints.unsubscribe(id)).data });
}
