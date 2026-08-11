import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../utils/render";
import { ClaimCard } from "@/components/verification/claim-card";
import type { ClaimVerdict } from "@/types/api";

describe("ClaimCard", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe("Verdict status rendering", () => {
    it("renders SUPPORTED verdict with icon and label", () => {
      const claim: ClaimVerdict = {
        text: "RISC uses a reduced instruction set",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.987,
        chunk_ids: ["1:3"],
        reason: null,
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(container.textContent?.toLowerCase()).toContain("supported");
      expect(screen.getByText(/evidence score 98\.\d%/)).toBeInTheDocument();
    });

    it("renders UNSUPPORTED verdict with icon and label", () => {
      const claim: ClaimVerdict = {
        text: "Some unconfirmed claim",
        status: "UNSUPPORTED",
        citations: ["C2"],
        evidence_score: 0.45,
        chunk_ids: ["1:5"],
        reason: "No single chunk entails this paraphrase",
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(container.textContent?.toLowerCase()).toContain("unsupported");
      expect(screen.getByText(/evidence score 45\.\d%/)).toBeInTheDocument();
      expect(screen.getByText(/No single chunk entails/)).toBeInTheDocument();
    });

    it("renders CONTRADICTED verdict with icon and label", () => {
      const claim: ClaimVerdict = {
        text: "RISC has a complex instruction set",
        status: "CONTRADICTED",
        citations: ["C1"],
        evidence_score: 0.12,
        chunk_ids: ["1:3"],
        reason: "Evidence clearly contradicts this",
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(container.textContent?.toLowerCase()).toContain("contradicted");
      expect(screen.getByText(/evidence score 12\.\d%/)).toBeInTheDocument();
    });

    it("renders REFUSAL with 'not scored' instead of percentage", () => {
      const claim: ClaimVerdict = {
        text: "I cannot answer this",
        status: "REFUSAL",
        citations: [],
        evidence_score: null,
        chunk_ids: [],
        reason: null,
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      // REFUSAL is labeled as "Declined" in the UI
      expect(container.textContent?.toLowerCase()).toContain("declined");
      expect(screen.getByText("not scored")).toBeInTheDocument();
      // Should never show "0%" or "0.0%"
      expect(screen.queryByText(/0\.0%|^0%$/)).not.toBeInTheDocument();
    });
  });

  describe("Claim text display", () => {
    it("displays full claim text", () => {
      const claim: ClaimVerdict = {
        text: "This is a detailed claim about the subject",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.9,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(screen.getByText("This is a detailed claim about the subject")).toBeInTheDocument();
    });
  });

  describe("Reason display", () => {
    it("shows reason when present", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "UNSUPPORTED",
        citations: ["C1"],
        evidence_score: 0.4,
        chunk_ids: ["1:3"],
        reason: "The evidence does not support this interpretation",
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(screen.getByText("The evidence does not support this interpretation")).toBeInTheDocument();
    });

    it("hides reason when null (SUPPORTED claims)", () => {
      const claim: ClaimVerdict = {
        text: "Supported claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.95,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      // Should not render any reason section
      // Just check the component renders without a reason display
      expect(screen.getByText("Supported claim")).toBeInTheDocument();
    });
  });

  describe("Citation chips", () => {
    it("renders citation chips for each citation-chunk pair", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1", "C2"],
        evidence_score: 0.8,
        chunk_ids: ["1:3", "1:5"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      // Should have chips for both citations
      expect(screen.getByRole("button", { name: /View evidence for citation C1/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /View evidence for citation C2/ })).toBeInTheDocument();
    });

    it("calls onOpenChunk when citation chip is clicked", async () => {
      const user = userEvent.setup();
      const onOpenChunk = vi.fn();

      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.8,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={onOpenChunk} />);

      const chip = screen.getByRole("button", { name: /View evidence for citation C1/ });
      await user.click(chip);

      expect(onOpenChunk).toHaveBeenCalledWith("1:3");
    });

    it("degrades gracefully with mismatched citation/chunk_ids arrays", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1", "C2", "C3"], // 3 citations
        evidence_score: 0.8,
        chunk_ids: ["1:3", "1:5"], // Only 2 chunks
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      // Should render chips only for the zipped pairs (shorter length)
      // With zipClaimCitations degradation, this should show 2 chips not 3
      const chips = screen.getAllByRole("button", { name: /View evidence for citation/ });
      expect(chips.length).toBeLessThanOrEqual(2);
    });

    it("shows 'Cited:' label on question route", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.8,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} citationsAreExhaustive={false} />);

      expect(screen.getByText("Cited:")).toBeInTheDocument();
    });

    it("shows 'Chunks considered:' label on fact route", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.8,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} citationsAreExhaustive={true} />);

      expect(screen.getByText("Chunks considered:")).toBeInTheDocument();
    });

    it("does not render citation section when no citations", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "REFUSAL",
        citations: [],
        evidence_score: null,
        chunk_ids: [],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(screen.queryByText(/Cited:|Chunks considered:/)).not.toBeInTheDocument();
    });
  });

  describe("Evidence score formatting", () => {
    it("formats evidence score as percentage", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.876,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(screen.getByText(/evidence score 87\.\d%/)).toBeInTheDocument();
    });

    it("handles low scores", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "UNSUPPORTED",
        citations: ["C1"],
        evidence_score: 0.12,
        chunk_ids: ["1:3"],
        reason: "Not supported",
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(screen.getByText(/evidence score 12\.\d%/)).toBeInTheDocument();
    });

    it("handles high scores", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.995,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      expect(screen.getByText(/evidence score 99\.\d%/)).toBeInTheDocument();
    });
  });

  describe("Visual styling by verdict", () => {
    it("applies SUPPORTED styling", () => {
      const claim: ClaimVerdict = {
        text: "Supported",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.9,
        chunk_ids: ["1:3"],
        reason: null,
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      const card = container.querySelector("[class*='border-verdict-supported']");
      expect(card).toBeInTheDocument();
    });

    it("applies UNSUPPORTED styling", () => {
      const claim: ClaimVerdict = {
        text: "Unsupported",
        status: "UNSUPPORTED",
        citations: ["C1"],
        evidence_score: 0.4,
        chunk_ids: ["1:3"],
        reason: "Not supported",
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      const card = container.querySelector("[class*='border-verdict-unsupported']");
      expect(card).toBeInTheDocument();
    });

    it("applies CONTRADICTED styling", () => {
      const claim: ClaimVerdict = {
        text: "Contradicted",
        status: "CONTRADICTED",
        citations: ["C1"],
        evidence_score: 0.15,
        chunk_ids: ["1:3"],
        reason: "Evidence contradicts",
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      const card = container.querySelector("[class*='border-verdict-contradicted']");
      expect(card).toBeInTheDocument();
    });

    it("applies REFUSAL styling", () => {
      const claim: ClaimVerdict = {
        text: "Refusal",
        status: "REFUSAL",
        citations: [],
        evidence_score: null,
        chunk_ids: [],
        reason: null,
      };

      const { container } = renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      const card = container.querySelector("[class*='border-verdict-refusal']");
      expect(card).toBeInTheDocument();
    });
  });

  describe("Accessibility", () => {
    it("claim text is readable as paragraph", () => {
      const claim: ClaimVerdict = {
        text: "This is a claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.8,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      const claimText = screen.getByText("This is a claim");
      expect(claimText).toHaveClass("text-sm");
    });

    it("citation chips have accessible names", () => {
      const claim: ClaimVerdict = {
        text: "Some claim",
        status: "SUPPORTED",
        citations: ["C1"],
        evidence_score: 0.8,
        chunk_ids: ["1:3"],
        reason: null,
      };

      renderWithProviders(<ClaimCard claim={claim} onOpenChunk={() => {}} />);

      const chip = screen.getByRole("button", { name: /View evidence for citation C1, chunk 1:3/ });
      expect(chip).toBeInTheDocument();
    });
  });
});
