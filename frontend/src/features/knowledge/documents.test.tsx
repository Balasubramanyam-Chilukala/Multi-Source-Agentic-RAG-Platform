import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { isApiError } from "@/api/client";
import { toApiError } from "@/api/client";
import type { DocumentStatus } from "@/api/types";
import { DocumentStatusBadge } from "@/components/badges";
import { DOCUMENT_POLL_MS, documentPollInterval, documentsListPollInterval, evaluationPollInterval } from "@/hooks/polling";
import { renderPage } from "@/test/utils";
import { duplicateDocumentId, validateFile } from "./AddSources";
import { DocumentStatusNote, UNSUPPORTED_FALLBACK } from "./DocumentStatusNote";

describe("document status badge", () => {
  it.each<[DocumentStatus, string]>([
    ["pending", "Pending"],
    ["processing", "Processing"],
    ["ready", "Ready"],
    ["failed", "Failed"],
    ["unsupported", "Unsupported"],
  ])("%s → %s", (status, label) => {
    renderPage(<DocumentStatusBadge status={status} />);
    expect(screen.getByText(label).closest("[data-status]")).toHaveAttribute("data-status", status);
  });

  it("explains unsupported documents (e.g. scanned PDFs needing OCR) and shows server errors for failed ones", () => {
    renderPage(
      <div>
        <DocumentStatusNote document={{ status: "unsupported", error_message: null }} />
        <DocumentStatusNote document={{ status: "failed", error_message: "PDF is encrypted" }} />
        <DocumentStatusNote document={{ status: "ready", error_message: null }} />
      </div>,
    );
    expect(screen.getByText(UNSUPPORTED_FALLBACK)).toBeInTheDocument();
    expect(screen.getByText(/OCR/)).toBeInTheDocument();
    expect(screen.getByText("PDF is encrypted")).toBeInTheDocument();
  });
});

describe("ingestion polling stop condition", () => {
  it("polls a single document only while pending/processing", () => {
    expect(documentPollInterval("pending")).toBe(DOCUMENT_POLL_MS);
    expect(documentPollInterval("processing")).toBe(DOCUMENT_POLL_MS);
    for (const terminal of ["ready", "failed", "unsupported"] as const) expect(documentPollInterval(terminal)).toBe(false);
    expect(documentPollInterval(undefined)).toBe(false);
  });

  it("polls the documents list while any document is in flight, and stops when all settle", () => {
    expect(documentsListPollInterval(["ready", "processing", "failed"])).toBe(DOCUMENT_POLL_MS);
    expect(documentsListPollInterval(["ready", "failed", "unsupported"])).toBe(false);
    expect(documentsListPollInterval([])).toBe(false);
  });

  it("polls evaluations while queued/running", () => {
    expect(evaluationPollInterval(["succeeded", "running"])).not.toBe(false);
    expect(evaluationPollInterval(["succeeded", "failed"])).toBe(false);
  });
});

describe("uploads", () => {
  it("validates extension and size client-side", () => {
    expect(validateFile(new File(["# hi"], "notes.md"), 1000)).toBeNull();
    expect(validateFile(new File(["x"], "image.png"))).toMatch(/Unsupported file type/);
    expect(validateFile(new File(["x".repeat(20)], "big.txt"), 10)).toMatch(/larger than/);
  });

  it("reads existing_document_id from a 409 duplicate response", async () => {
    const response = new Response(
      JSON.stringify({ error: { code: "conflict", message: "Already uploaded", request_id: "r", details: [{ existing_document_id: "doc-42" }] } }),
      { status: 409 },
    );
    const error = await toApiError(response);
    expect(isApiError(error)).toBe(true);
    expect(duplicateDocumentId(error)).toBe("doc-42");
  });
});
