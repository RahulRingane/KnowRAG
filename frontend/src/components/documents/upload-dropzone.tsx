"use client";

import { useId, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { UploadCloud } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface UploadDropzoneProps {
  onFileSelected: (file: File) => void;
  disabled?: boolean;
  /** Inline validation/error message shown under the dropzone. Owned by
   *  the caller so a server-side rejection (`ApiError.detail` from a
   *  `400`) can be surfaced through the same slot as a client-side
   *  extension check. */
  errorMessage?: string | null;
}

const ACCEPTED_EXTENSION = ".pdf";

function hasPdfExtension(filename: string): boolean {
  return filename.toLowerCase().endsWith(ACCEPTED_EXTENSION);
}

/**
 * Drag-and-drop PDF upload zone with a keyboard-reachable file-input
 * fallback (§5.3 a11y floor: "The drag-and-drop zone must have a
 * keyboard-reachable file-input equivalent — drag-only is not
 * accessible"). The real `<input type="file">` is always present and
 * focusable — visually de-emphasised with `sr-only` but never `hidden` —
 * so Tab + Enter/Space opens the OS file picker exactly like a pointer
 * click on "Browse files" does; the whole element stays in the tab order
 * regardless of drag support.
 *
 * Client-side `.pdf` extension validation happens here as a fast first
 * line of defence with no round trip. The backend is still the authority
 * and rejects non-PDF uploads by extension with a `400` regardless
 * (domain trap #3) — that server-side rejection is surfaced by the caller
 * through `errorMessage`, not by this component, since only the caller
 * knows about a mutation's result.
 */
export function UploadDropzone({ onFileSelected, disabled, errorMessage }: UploadDropzoneProps) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  function acceptOrReject(file: File | undefined) {
    if (!file) return;
    if (!hasPdfExtension(file.name)) {
      setLocalError(`"${file.name}" isn't a PDF. Only .pdf files can be ingested.`);
      return;
    }
    setLocalError(null);
    onFileSelected(file);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    acceptOrReject(event.dataTransfer.files[0]);
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    acceptOrReject(event.target.files?.[0]);
    // Reset so selecting the same filename twice in a row still fires onChange.
    event.target.value = "";
  }

  const message = errorMessage ?? localError;

  return (
    <div className="space-y-2">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={cn(
          "border-border flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors",
          isDragging && "border-primary bg-primary/5",
          disabled && "pointer-events-none opacity-50",
        )}
      >
        <UploadCloud className="text-muted-foreground size-8" aria-hidden="true" />
        <div className="space-y-1">
          <p className="text-sm font-medium">Drag a PDF here</p>
          <p className="text-muted-foreground text-sm">or</p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => inputRef.current?.click()}
          disabled={disabled}
        >
          Browse files
        </Button>
        <label htmlFor={inputId} className="sr-only">
          Choose a PDF file to ingest
        </label>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          onChange={handleChange}
          disabled={disabled}
        />
      </div>
      {message ? (
        <p role="alert" className="text-destructive text-sm">
          {message}
        </p>
      ) : null}
    </div>
  );
}
