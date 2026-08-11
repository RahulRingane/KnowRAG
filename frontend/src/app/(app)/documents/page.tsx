import { DocumentList } from "@/components/documents/document-list";
import { DocumentUploadPanel } from "@/components/documents/document-upload-panel";

/**
 * Server component composing the two client islands: the upload lifecycle
 * (`DocumentUploadPanel`) and the ingested-document list (`DocumentList`).
 * They share the TanStack Query cache via the `documents` query key, so an
 * upload reaching a terminal status refreshes the list with no page
 * reload (frontend_plan.md §6, WS-E).
 */
export default function DocumentsPage() {
  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Documents</h1>
        <p className="text-muted-foreground">
          Upload a PDF to add it to the corpus. Accepted (<code>202</code>) doesn&apos;t mean
          searchable yet — wait for indexing to finish before querying it.
        </p>
      </div>

      <DocumentUploadPanel />

      <div className="space-y-3">
        <h2 className="text-lg font-medium">Ingested documents</h2>
        <DocumentList />
      </div>
    </div>
  );
}
