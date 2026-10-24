import type { DocumentStatus, EvaluationStatus } from "@/api/types";

/** Poll intervals while work is in flight; `false` stops polling (TanStack Query semantics). */
export const DOCUMENT_POLL_MS = 1500;
export const EVALUATION_POLL_MS = 2500;

export function isDocumentInFlight(status: DocumentStatus | undefined): boolean {
  return status === "pending" || status === "processing";
}

export function documentPollInterval(status: DocumentStatus | undefined): number | false {
  return isDocumentInFlight(status) ? DOCUMENT_POLL_MS : false;
}

export function documentsListPollInterval(statuses: DocumentStatus[] | undefined): number | false {
  return statuses?.some(isDocumentInFlight) ? DOCUMENT_POLL_MS : false;
}

export function isEvaluationActive(status: EvaluationStatus | undefined): boolean {
  return status === "queued" || status === "running";
}

export function evaluationPollInterval(statuses: Array<EvaluationStatus | undefined>): number | false {
  return statuses.some(isEvaluationActive) ? EVALUATION_POLL_MS : false;
}
