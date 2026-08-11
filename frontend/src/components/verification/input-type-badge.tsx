import { HelpCircle, MessageSquareQuote } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { InputType } from "@/types/api";

/**
 * Shows which route the backend actually ran, from the AUTHORITATIVE
 * `input_type` field on the response — distinct from
 * `query/route-hint-badge.tsx`, which is a client-side pre-submit guess.
 * Trap #8 of the WS-D brief calls routing invisible-by-default "the most
 * confusing thing about this API from outside"; this badge is the fix, and
 * unlike the hint it is never wrong, because it's just echoing back what
 * the server decided.
 */
export function InputTypeBadge({ inputType }: { inputType: InputType }) {
  const isQuestion = inputType === "question";

  return (
    <Tooltip>
      <TooltipTrigger render={<Badge variant="secondary" className="gap-1" />}>
        {isQuestion ? (
          <HelpCircle aria-hidden="true" data-icon="inline-start" />
        ) : (
          <MessageSquareQuote aria-hidden="true" data-icon="inline-start" />
        )}
        {isQuestion ? "Question route" : "Fact-check route"}
      </TooltipTrigger>
      <TooltipContent>
        {isQuestion
          ? "Retrieved evidence, generated an answer from it, then verified each claim."
          : "Retrieved evidence and scored your own statement against it directly — no answer was generated."}
      </TooltipContent>
    </Tooltip>
  );
}
