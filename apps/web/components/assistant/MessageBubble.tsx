"use client";

import Image from "next/image";
import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

import { RecommendationCard } from "@/components/recommendation/RecommendationCard";
import { AssessmentProgress } from "@/components/trip/AssessmentProgress";
import type { RunProgress } from "@/features/assessment/useRunEvents";
import { useRecommendation } from "@/features/trips/hooks";
import type { ConversationMessage } from "@/lib/api/generated/contracts";
import { formatTime } from "@/lib/utils";

// Text is user-authored or server summaries: markdown is sanitized, links open in a new tab, no raw HTML.
const SCHEMA = { ...defaultSchema, tagNames: ["p", "strong", "em", "ul", "ol", "li", "a", "br", "code"], attributes: { a: ["href"] } };

export function SafeMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      rehypePlugins={[[rehypeSanitize, SCHEMA]]}
      components={{
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary-deep underline">
            {children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

export function MessageBubble({ message, tripId, progress }: { message: ConversationMessage; tripId: string | null; progress?: RunProgress }) {
  const rec = useRecommendation(message.recommendation_id);
  const isUser = message.role === "user";
  return (
    <li className={`flex items-end gap-2 ${isUser ? "justify-end" : ""}`}>
      {!isUser && <Image src="/assets/mascot/mascot-welcome.png" alt="" width={44} height={44} className="rounded-full bg-mint" />}
      <div className={`max-w-[85%] ${isUser ? "text-right" : ""}`}>
        {message.text && (
          <div className={`inline-block rounded-2xl px-4 py-2 text-left text-sm ${isUser ? "rounded-br-sm bg-mint text-navy" : "rounded-bl-sm bg-surface-secondary text-navy"}`}>
            <SafeMarkdown text={message.text} />
          </div>
        )}
        {!isUser && message.recommendation_id && tripId && (
          <div className="mt-2 text-left">
            {rec.data ? <RecommendationCard rec={rec.data} tripId={tripId} /> : <div className="h-24 animate-pulse rounded-2xl bg-surface-secondary" />}
          </div>
        )}
        {!isUser && !message.recommendation_id && progress && (
          <div className="mt-2 text-left">
            <AssessmentProgress progress={progress} />
          </div>
        )}
        <p className="mt-1 text-[11px] text-ink-muted">{formatTime(message.created_at)}</p>
      </div>
      {isUser && <span className="sta-icon-tile !w-9 !h-9 rounded-full bg-white text-primary-deep border border-line text-xs font-bold">You</span>}
    </li>
  );
}
