import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import type { RunOutcome } from "@/api/types";
import { qk } from "./keys";

export function useRuns(outcome: RunOutcome | undefined, offset: number, limit = 25) {
  return useQuery({
    queryKey: qk.runs(outcome, offset),
    queryFn: () => api.runs.list({ outcome, offset, limit }),
    placeholderData: keepPreviousData,
  });
}

export function useRun(id: string | undefined) {
  return useQuery({ queryKey: qk.run(id ?? ""), queryFn: () => api.runs.get(id as string), enabled: Boolean(id) });
}
