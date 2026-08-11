"use client";

import { type KeyboardEvent } from "react";
import { Radio, RadioTower, Send, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { RouteHintBadge } from "./route-hint-badge";

export interface QueryComposerProps {
  value: string;
  onValueChange: (value: string) => void;
  streaming: boolean;
  onStreamingChange: (streaming: boolean) => void;
  onSubmit: () => void;
  onCancel: () => void;
  /** True whenever a request is in flight, streaming or not — disables the
   *  textarea and the streaming toggle (changing transport mid-flight would
   *  orphan the in-flight call) and swaps the submit button for cancel. */
  isBusy: boolean;
}

/**
 * The question/statement input. A single field serves both routes (§1.1:
 * the request body field is always named `question`, even for a fact-check
 * statement — the backend classifies, not the caller), so this component
 * has no "mode" of its own beyond the streaming toggle.
 */
export function QueryComposer({
  value,
  onValueChange,
  streaming,
  onStreamingChange,
  onSubmit,
  onCancel,
  isBusy,
}: QueryComposerProps) {
  const canSubmit = value.trim().length > 0 && !isBusy;

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (canSubmit) onSubmit();
    }
  }

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) onSubmit();
      }}
    >
      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="query-input">Ask a question, or state something to fact-check</Label>
          {value.trim().length > 0 ? <RouteHintBadge text={value} /> : null}
        </div>
        <Textarea
          id="query-input"
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder='"What is RISC?" or "RISC has instruction pipelining"'
          disabled={isBusy}
          minLength={1}
          rows={3}
          aria-describedby="query-input-hint"
        />
        <p id="query-input-hint" className="text-muted-foreground text-xs">
          A trailing “?” (or an opening word like “what”/“is”/“does”) routes to an
          answer; anything else is checked as a statement against the evidence.
          Press ⌘/Ctrl + Enter to submit.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          variant={streaming ? "default" : "outline"}
          size="sm"
          aria-pressed={streaming}
          disabled={isBusy}
          onClick={() => onStreamingChange(!streaming)}
        >
          {streaming ? (
            <RadioTower aria-hidden="true" data-icon="inline-start" />
          ) : (
            <Radio aria-hidden="true" data-icon="inline-start" />
          )}
          Streaming {streaming ? "on" : "off"}
        </Button>

        <div className="flex items-center gap-2">
          {isBusy ? (
            <Button type="button" variant="outline" size="sm" onClick={onCancel}>
              <X aria-hidden="true" data-icon="inline-start" />
              Cancel
            </Button>
          ) : null}
          <Button type="submit" size="sm" disabled={!canSubmit}>
            <Send aria-hidden="true" data-icon="inline-start" />
            {isBusy ? "Working…" : "Submit"}
          </Button>
        </div>
      </div>
    </form>
  );
}
