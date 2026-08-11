import { Suspense } from "react";

import { CardSkeleton } from "@/components/layout/skeletons";
import { QueryWorkspace } from "@/components/query/query-workspace";

/**
 * The product's home route: query in, fact-checked result out. Stays a
 * server component per §5.1/§5.3 ("pages stay server components delegating
 * to islands") — all the interactivity (composer state, streaming,
 * evidence panel) lives in `QueryWorkspace`, the one client island this
 * page mounts. This is also the page's one `<h1>` (§5.3's a11y floor);
 * nothing under `components/query` or `components/verification` renders
 * its own.
 *
 * --- `<Suspense>` boundary (integration-time addition) ---
 * `QueryWorkspace` reads `useSearchParams()` to prefill the composer from
 * `/history`'s "re-run" link (`?q=...`). In the Next.js App Router, a
 * client component calling `useSearchParams()` opts its nearest render
 * tree out of static prerendering *unless* it's wrapped in `<Suspense>` —
 * without this boundary, `next build` would either error or silently
 * deopt `/` from `○ (Static)` to a dynamic route. The boundary keeps this
 * page's static shell (this file) prerendered while only the search-param
 * read inside `QueryWorkspace` becomes a client-resolved island. Do not
 * remove this wrapper when touching this file, and re-check `next build`'s
 * route table (`○` vs `ƒ` next to `/`) if you do.
 */
export default function QueryPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Query</h1>
        <p className="text-muted-foreground">
          Ask a question to get an evidence-checked answer, or state something to
          fact-check it directly against the corpus.
        </p>
      </div>
      <Suspense fallback={<CardSkeleton />}>
        <QueryWorkspace />
      </Suspense>
    </div>
  );
}
