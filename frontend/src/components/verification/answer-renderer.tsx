import { parseCitations } from "@/lib/utils/citations";

import { CitationChip } from "./citation-chip";
import { resolveAnswerCitation } from "./citation-resolution";

/**
 * Renders `FactCheckedResponse.answer`, which carries literal `[C1][C3]`
 * citation tags (trap #3 — `domain/assembly.py`), as prose interspersed
 * with interactive citation chips instead of raw bracketed text.
 * `parseCitations` (WS-B, `lib/utils/citations.ts`) is the only citation
 * parser in this codebase; this component consumes its output rather than
 * writing a second regex.
 *
 * On the fact route, `answer` is a fixed canned sentence
 * (`FACT_SUPPORTED_MESSAGE` and friends) that carries no `[Cn]` tags at
 * all — `parseCitations` on plain prose just returns one `text` segment, so
 * this component needs no route-specific branch to handle that correctly.
 */
export function AnswerRenderer({
  answer,
  retrievedChunkIds,
  onOpenChunk,
}: {
  answer: string;
  retrievedChunkIds: string[];
  onOpenChunk: (chunkId: string) => void;
}) {
  const segments = parseCitations(answer);

  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap">
      {segments.map((segment, i) =>
        segment.type === "text" ? (
          <span key={i}>{segment.text}</span>
        ) : (
          <CitationChip
            key={i}
            tag={segment.tag}
            chunkId={resolveAnswerCitation(segment.tag, retrievedChunkIds)}
            onOpen={onOpenChunk}
          />
        ),
      )}
    </p>
  );
}
