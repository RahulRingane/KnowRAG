import { ListSkeleton } from "@/components/layout/skeletons";

/** Root-level Suspense fallback for route transitions. Individual pages
 *  render their own, more specific skeletons once they touch the API. */
export default function Loading() {
  return (
    <div className="space-y-4 py-4" role="status" aria-live="polite" aria-label="Loading">
      <ListSkeleton rows={3} />
    </div>
  );
}
