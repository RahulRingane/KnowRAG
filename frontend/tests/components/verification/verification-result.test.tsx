import { describe, expect, it, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../utils/render";
import { VerificationResult } from "@/components/verification/verification-result";
import {
  questionRouteOkResponse,
  questionRouteInsufficientResponse,
  questionRouteWithContradictionResponse,
  factRouteSupportedResponse,
  factRouteContradictedResponse,
  mixedClaimsResponse,
} from "./fixtures";

describe("VerificationResult", () => {
  const mockOnOpenChunk = () => {};

  beforeEach(() => {
    localStorage.clear();
  });

  describe("Trap #1: insufficient_evidence is NOT an error", () => {
    it("renders insufficient_evidence state as normal, not error", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteInsufficientResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Should show the state banner without error styling
      const banner = screen.getByText(/Insufficient evidence/i);
      expect(banner).toBeInTheDocument();

      // Banner should have HelpCircle icon and descriptive text
      const bannerSection = banner.closest("[role='alert']");
      expect(bannerSection).toHaveTextContent(
        /Nothing answerable could be generated and confirmed/i
      );

      // Should NOT render as an error state with destructive colors
      // The className should use verdict-unsupported, not error classes
      expect(bannerSection).toHaveClass("bg-verdict-unsupported-bg");
    });

    it("still renders answer and claims even when insufficient_evidence", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteInsufficientResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Should show the answer section
      expect(screen.getByText(/does not contain sufficient information/i)).toBeInTheDocument();

      // Should render the claims section
      expect(screen.getByText(/All claims/i)).toBeInTheDocument();
    });
  });

  describe("Trap #2: Claim can appear in both answer and rejected_claims", () => {
    it("shows 'Needs scrutiny' tab with rejected claims", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Go to "Needs scrutiny" tab to see rejected claims
      const needsScrutinyTab = screen.getByRole("tab", { name: /Needs scrutiny/ });
      expect(needsScrutinyTab).toBeInTheDocument();

      needsScrutinyTab.click();

      // Should show the rejected claims section
      expect(screen.getByText(/Needs scrutiny/)).toBeInTheDocument();
    });
  });

  describe("Trap #3: CONTRADICTED claims withheld from answer but in claims", () => {
    it("withholds CONTRADICTED claims from answer but shows in claims", () => {
      renderWithProviders(
        <VerificationResult
          response={questionRouteWithContradictionResponse}
          onOpenChunk={mockOnOpenChunk}
        />
      );

      const claimText = "RISC has a complex instruction set";

      // SHOULD appear in All claims tab
      const allClaimsTab = screen.getByRole("tab", { name: /All claims/ });
      allClaimsTab.click();

      expect(screen.getByText(claimText)).toBeInTheDocument();

      // And in "Needs scrutiny" (rejected_claims)
      const needsScrutinyTab = screen.getByRole("tab", { name: /Needs scrutiny/ });
      needsScrutinyTab.click();

      expect(screen.getByText(claimText)).toBeInTheDocument();
    });
  });

  describe("Trap #4: All four verdict statuses render distinctly", () => {
    it("renders SUPPORTED verdict with icon and text", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // SUPPORTED should show with its own icon and label
      // VERDICT_META maps SUPPORTED → "Supported" label
      expect(screen.getByText("Supported")).toBeInTheDocument();
      expect(screen.getByText(/evidence score 98\.\d%/)).toBeInTheDocument();
    });

    it("renders UNSUPPORTED verdict with icon and text", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // VERDICT_META maps UNSUPPORTED → "Unsupported" label
      expect(screen.getByText("Unsupported")).toBeInTheDocument();
      expect(screen.getByText(/evidence score 45\.\d%/)).toBeInTheDocument();
    });

    it("renders CONTRADICTED verdict with icon and text", () => {
      renderWithProviders(
        <VerificationResult
          response={questionRouteWithContradictionResponse}
          onOpenChunk={mockOnOpenChunk}
        />
      );

      const allClaimsTab = screen.getByRole("tab", { name: /All claims/ });
      allClaimsTab.click();

      // VERDICT_META maps CONTRADICTED → "Contradicted" label
      expect(screen.getByText("Contradicted")).toBeInTheDocument();
      expect(screen.getByText(/evidence score 12\.\d%/)).toBeInTheDocument();
    });

    it("renders REFUSAL with 'not scored' label instead of 0%", () => {
      const refusalResponse = {
        ...questionRouteOkResponse,
        claims: [
          {
            text: "I cannot answer this",
            status: "REFUSAL" as const,
            citations: [],
            evidence_score: null,
            chunk_ids: [],
            reason: null,
          },
        ],
        refusals: [
          {
            text: "I cannot answer this",
            status: "REFUSAL" as const,
            citations: [],
            evidence_score: null,
            chunk_ids: [],
            reason: null,
          },
        ],
      };

      renderWithProviders(
        <VerificationResult response={refusalResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // VERDICT_META maps REFUSAL → "Declined" label
      expect(screen.getByText("Declined")).toBeInTheDocument();
      // Should show "not scored", never "0%"
      expect(screen.getByText("not scored")).toBeInTheDocument();
      expect(screen.queryByText(/0%|0\.0%/)).not.toBeInTheDocument();
    });
  });

  describe("Trap #5: Fact route response", () => {
    it("shows input_type badge indicating fact route", () => {
      renderWithProviders(
        <VerificationResult response={factRouteSupportedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      const factBadge = screen.getByText("Fact-check route");
      expect(factBadge).toBeInTheDocument();
    });

    it("has exactly one claim on fact route", () => {
      renderWithProviders(
        <VerificationResult response={factRouteSupportedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Count claim cards (they have verdict status badges with labels from VERDICT_META)
      const claimBadges = screen.getAllByText(/^(Supported|Unsupported|Contradicted|Declined)$/);
      expect(claimBadges).toHaveLength(1);
    });

    it("shows canned answer sentence on fact route", () => {
      renderWithProviders(
        <VerificationResult response={factRouteSupportedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      expect(screen.getByText(/This statement is supported/)).toBeInTheDocument();
    });

    it("has no generation_ms key in latency breakdown", () => {
      renderWithProviders(
        <VerificationResult response={factRouteSupportedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // The latency keys should be listed
      expect(screen.getByText(/retrieval/i)).toBeInTheDocument();
      expect(screen.getByText(/verification/i)).toBeInTheDocument();

      // But NOT generation_ms
      expect(screen.queryByText(/generation/i)).not.toBeInTheDocument();
    });

    it("labels citations as 'Chunks considered' on fact route, not 'Cited'", () => {
      renderWithProviders(
        <VerificationResult response={factRouteSupportedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // On fact route, citations are "everything considered", not corroboration
      expect(screen.getByText(/Chunks considered:/)).toBeInTheDocument();
      expect(screen.queryByText(/^Cited:$/)).not.toBeInTheDocument();
    });
  });

  describe("Trap #6: latency_ms is open-ended", () => {
    it("renders unknown/unexpected latency keys without crashing", () => {
      const unknownKeyResponse = {
        ...questionRouteOkResponse,
        latency_ms: {
          retrieval_ms: 100,
          custom_metric_ms: 50,
          another_unknown_stage_ms: 200,
          generation_ms: 100,
        },
      };

      renderWithProviders(
        <VerificationResult response={unknownKeyResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Should render all keys, including unknown ones
      expect(screen.getByText(/custom_metric/)).toBeInTheDocument();
      expect(screen.getByText(/another_unknown_stage/)).toBeInTheDocument();
      expect(screen.getByText(/retrieval/)).toBeInTheDocument();
      expect(screen.getByText(/generation/)).toBeInTheDocument();
    });

    it("renders fact route response with no generation_ms", () => {
      renderWithProviders(
        <VerificationResult response={factRouteSupportedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Should only render keys that exist
      expect(screen.getByText(/retrieval/i)).toBeInTheDocument();
      expect(screen.getByText(/verification/i)).toBeInTheDocument();
      expect(screen.queryByText(/generation/)).not.toBeInTheDocument();
    });
  });

  describe("Trap #7: Multiple state values render distinctly", () => {
    it("renders 'ok' state with CheckCircle icon", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      const banner = screen.getByText("Answer generated");
      expect(banner).toBeInTheDocument();

      const bannerSection = banner.closest("[role='alert']");
      expect(bannerSection).toHaveClass("bg-muted/50");
    });

    it("renders 'insufficient_evidence' state distinctly", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteInsufficientResponse} onOpenChunk={mockOnOpenChunk} />
      );

      const banner = screen.getByText(/Insufficient evidence/i);
      expect(banner).toBeInTheDocument();

      const bannerSection = banner.closest("[role='alert']");
      expect(bannerSection).toHaveClass("bg-verdict-unsupported-bg");
    });

    it("renders 'contradicted' state distinctly (fact route)", () => {
      renderWithProviders(
        <VerificationResult response={factRouteContradictedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      const banner = screen.getByText(/Contradicted by evidence/i);
      expect(banner).toBeInTheDocument();

      const bannerSection = banner.closest("[role='alert']");
      expect(bannerSection).toHaveClass("bg-verdict-contradicted-bg");
    });
  });

  describe("Accessibility", () => {
    it("has aria-live on result region for screen readers", () => {
      const { container } = renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      const resultRegion = container.querySelector("[aria-live='polite']");
      expect(resultRegion).toBeInTheDocument();
    });

    it("has h2 for Answer section", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      const answerHeading = screen.getByRole("heading", { level: 2, name: /Answer/i });
      expect(answerHeading).toBeInTheDocument();
    });

    it("provides accessible claim status information via icon and text", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Each claim should have both icon (aria-hidden) and text status
      // VERDICT_META renders "Supported" not "SUPPORTED"
      const supportedStatus = screen.getByText("Supported");
      expect(supportedStatus).toBeInTheDocument();

      // The icon should be aria-hidden
      const statusCard = supportedStatus.closest(".inline-flex");
      const icon = statusCard?.querySelector("[aria-hidden='true']");
      expect(icon).toBeInTheDocument();
    });
  });

  describe("Input type badge visibility", () => {
    it("shows question route badge for question input_type", () => {
      renderWithProviders(
        <VerificationResult response={questionRouteOkResponse} onOpenChunk={mockOnOpenChunk} />
      );

      expect(screen.getByText("Question route")).toBeInTheDocument();
    });

    it("shows fact-check route badge for fact input_type", () => {
      renderWithProviders(
        <VerificationResult response={factRouteSupportedResponse} onOpenChunk={mockOnOpenChunk} />
      );

      expect(screen.getByText("Fact-check route")).toBeInTheDocument();
    });
  });

  describe("Claims panel tabs", () => {
    it("shows all three tabs in claims panel", () => {
      renderWithProviders(
        <VerificationResult response={mixedClaimsResponse} onOpenChunk={mockOnOpenChunk} />
      );

      expect(screen.getByRole("tab", { name: /All claims/ })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: /Needs scrutiny/ })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: /Declined/ })).toBeInTheDocument();
    });

    it("displays claim counts in tab labels", () => {
      renderWithProviders(
        <VerificationResult response={mixedClaimsResponse} onOpenChunk={mockOnOpenChunk} />
      );

      // Mixed response has 3 claims total, 1 rejected, 0 refusals
      expect(screen.getByRole("tab", { name: /All claims \(3\)/ })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: /Needs scrutiny \(1\)/ })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: /Declined \(0\)/ })).toBeInTheDocument();
    });
  });
});
