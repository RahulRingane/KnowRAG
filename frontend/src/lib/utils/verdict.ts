/**
 * Presentation-only mapping from an NLI verdict status to its design-token
 * colour, human label, and icon. This is the ONLY place a verdict status is
 * mapped to a colour or icon — no downstream component should hardcode one
 * (§5.3). Verdicts must never be conveyed by colour alone: every consumer of
 * this map is expected to render both `icon` and `label`.
 *
 * This module does NOT define API types. `WS-B`'s `ClaimStatus` in
 * `src/types/api.ts` is a superset-compatible string union
 * ("SUPPORTED" | "UNSUPPORTED" | "CONTRADICTED" | "REFUSAL") and can be
 * passed here directly.
 */

import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  HelpCircle,
  type LucideIcon,
} from "lucide-react";

export type VerdictStatus = "SUPPORTED" | "UNSUPPORTED" | "CONTRADICTED" | "REFUSAL";

export interface VerdictMeta {
  /** Verdict this metadata describes. */
  status: VerdictStatus;
  /** Short label for badges/chips. */
  label: string;
  /** One-sentence description of what the verdict means, for tooltips/help text. */
  description: string;
  /** Lucide icon component — always render alongside `label`, never colour alone. */
  icon: LucideIcon;
  /** CSS variable (unwrapped, no `var()`) holding the accent colour — use via
   *  the `text-verdict-*` / `border-verdict-*` Tailwind utilities defined in
   *  globals.css, not by reading this string directly. */
  colorVar: `--verdict-${string}`;
  /** CSS variable for a low-emphasis background wash (badges, alert fills). */
  bgVar: `--verdict-${string}-bg`;
  /** Tailwind utility class for text/icon colour. */
  textClassName: string;
  /** Tailwind utility class for a soft background fill. */
  bgClassName: string;
  /** Tailwind utility class for a matching border colour. */
  borderClassName: string;
}

export const VERDICT_META: Record<VerdictStatus, VerdictMeta> = {
  SUPPORTED: {
    status: "SUPPORTED",
    label: "Supported",
    description: "The retrieved evidence confirms this claim.",
    icon: CheckCircle2,
    colorVar: "--verdict-supported",
    bgVar: "--verdict-supported-bg",
    textClassName: "text-verdict-supported",
    bgClassName: "bg-verdict-supported-bg",
    borderClassName: "border-verdict-supported",
  },
  UNSUPPORTED: {
    status: "UNSUPPORTED",
    label: "Unsupported",
    description:
      "The evidence could not confirm this claim. On a generated answer this is a normal, non-failure outcome — not a hallucination signal.",
    icon: HelpCircle,
    colorVar: "--verdict-unsupported",
    bgVar: "--verdict-unsupported-bg",
    textClassName: "text-verdict-unsupported",
    bgClassName: "bg-verdict-unsupported-bg",
    borderClassName: "border-verdict-unsupported",
  },
  CONTRADICTED: {
    status: "CONTRADICTED",
    label: "Contradicted",
    description: "The evidence actively disagrees with this claim.",
    icon: AlertTriangle,
    colorVar: "--verdict-contradicted",
    bgVar: "--verdict-contradicted-bg",
    textClassName: "text-verdict-contradicted",
    bgClassName: "bg-verdict-contradicted-bg",
    borderClassName: "border-verdict-contradicted",
  },
  REFUSAL: {
    status: "REFUSAL",
    label: "Declined",
    description: "The system declined to answer; nothing here was scored against evidence.",
    icon: CircleSlash,
    colorVar: "--verdict-refusal",
    bgVar: "--verdict-refusal-bg",
    textClassName: "text-verdict-refusal",
    bgClassName: "bg-verdict-refusal-bg",
    borderClassName: "border-verdict-refusal",
  },
};

export function getVerdictMeta(status: VerdictStatus): VerdictMeta {
  return VERDICT_META[status];
}
