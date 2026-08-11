import { z } from "zod";

/** Mirrors `AuthUser` / `TokenPair` / `Credentials` from `src/types/api.ts`. */

export const AuthUserSchema = z.object({
  id: z.number(),
  username: z.string(),
  created_at: z.string(),
});

export const TokenPairSchema = z.object({
  access_token: z.string(),
  token_type: z.literal("bearer"),
  expires_in: z.number(),
});

export const CredentialsSchema = z.object({
  username: z.string().min(1),
  password: z.string().min(1),
});
