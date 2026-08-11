import { HelpCircle, MessageSquareQuote } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { classifyHint } from "./classify-hint";

/**
 * Shows the caller a PREDICTION of which route `POST /query` will take,
 * computed client-side from `classifyHint` (a hand-port of the backend
 * heuristic). This exists because trap #8 of the WS-D brief calls routing
 * "the most confusing thing about this API from outside" — but the
 * prediction can disagree with the server's own classification (the two
 * implementations aren't guaranteed byte-identical, and the server is
 * authoritative regardless). The label says "predicted" and the tooltip
 * spells out why, on purpose: this badge must never be mistaken for the
 * `input_type` the response actually reports
 * (`verification/input-type-badge.tsx`, rendered after the call returns).
 */
export function RouteHintBadge({ text }: { text: string }) {
  const hint = classifyHint(text);
  const isQuestion = hint === "question";

  return (
    <Tooltip>
      <TooltipTrigger render={<Badge variant="outline" className="gap-1 text-muted-foreground" />}>
        {isQuestion ? (
          <HelpCircle aria-hidden="true" data-icon="inline-start" />
        ) : (
          <MessageSquareQuote aria-hidden="true" data-icon="inline-start" />
        )}
        Predicted: {isQuestion ? "question" : "fact-check"}
      </TooltipTrigger>
      <TooltipContent>
        A client-side guess only, from the same trailing-“?”/opening-word rule the
        backend uses — not the routing decision. The actual route is shown on the
        result after you submit.
      </TooltipContent>
    </Tooltip>
  );
}
