import { z } from "zod";

/**
 * The ONLY place in this codebase that reads `process.env` (§5.3 of
 * frontend_plan.md). Every other module imports `env` from here.
 *
 * `NEXT_PUBLIC_API_BASE_URL` must be a valid absolute URL. It is validated
 * once, at module load, so a misconfigured deployment fails immediately and
 * loudly instead of producing silent fetch failures deep in the app.
 */

const envSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z
    .url({
      message:
        'NEXT_PUBLIC_API_BASE_URL must be a valid absolute URL (e.g. "http://localhost:8000"). ' +
        "Set it in frontend/.env.local — see .env.example.",
    })
    .default("http://localhost:8000"),
});

function loadEnv() {
  const parsed = envSchema.safeParse({
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  });

  if (!parsed.success) {
    const message = parsed.error.issues.map((issue) => issue.message).join("; ");
    throw new Error(`[config/env] Invalid environment configuration: ${message}`);
  }

  return parsed.data;
}

const validated = loadEnv();

export const env = {
  apiBaseUrl: validated.NEXT_PUBLIC_API_BASE_URL,
} as const;
