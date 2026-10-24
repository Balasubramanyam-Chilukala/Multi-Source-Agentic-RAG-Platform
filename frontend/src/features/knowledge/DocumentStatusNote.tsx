import { AlertTriangle, Ban } from "lucide-react";
import type { DocumentOut } from "@/api/types";

export const UNSUPPORTED_FALLBACK =
  "No extractable text was found. Scanned PDFs and images need OCR, which this server does not perform.";

/** Explains failed / unsupported documents using the server's error_message. */
export function DocumentStatusNote({ document, compact }: { document: Pick<DocumentOut, "status" | "error_message">; compact?: boolean }) {
  if (document.status === "failed") {
    return (
      <p className={compact ? "line-clamp-2 text-xs text-danger" : "flex gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"}>
        {!compact && <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />}
        {document.error_message || "Ingestion failed."}
      </p>
    );
  }
  if (document.status === "unsupported") {
    return (
      <p className={compact ? "line-clamp-2 text-xs text-warning" : "flex gap-2 rounded-md bg-warning-soft px-3 py-2 text-sm text-warning"}>
        {!compact && <Ban className="mt-0.5 size-4 shrink-0" aria-hidden />}
        {document.error_message || UNSUPPORTED_FALLBACK}
      </p>
    );
  }
  return null;
}
