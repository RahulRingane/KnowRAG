import { z } from "zod";

/**
 * Mirrors `HealthCheck` / `HealthReport` from `src/types/api.ts`.
 * `GET /health` returns the SAME body shape on `200` and `503` (plan
 * §1.4) — `lib/api/endpoints.ts#health()` treats both statuses as a
 * successful, schema-validated response rather than throwing on the 503,
 * so this schema is applied uniformly regardless of status code.
 */

export const HealthCheckSchema = z.object({
  status: z.enum(["ok", "error"]),
  detail: z.string().optional(),
});

export const HealthReportSchema = z.object({
  status: z.enum(["ok", "degraded"]),
  service: z.string(),
  checks: z.object({
    postgres: HealthCheckSchema,
    qdrant: HealthCheckSchema,
    elasticsearch: HealthCheckSchema,
  }),
});
