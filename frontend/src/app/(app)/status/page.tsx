import { HealthPanel } from "@/components/system/health-panel";

/**
 * Server component delegating to `HealthPanel`, the client island that
 * polls `GET /health` (frontend_plan.md §6, WS-E).
 */
export default function StatusPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Status</h1>
        <p className="text-muted-foreground">
          Live connectivity to every datastore the API depends on: Postgres, Qdrant, Elasticsearch.
        </p>
      </div>
      <HealthPanel />
    </div>
  );
}
