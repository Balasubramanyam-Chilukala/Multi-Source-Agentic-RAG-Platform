import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import type { EvaluationFilters, EvaluationRunRequest } from "@/api/types";
import { qk } from "./keys";
import { evaluationPollInterval } from "./polling";

export function useEvaluationDatasets() {
  return useQuery({ queryKey: qk.datasets, queryFn: api.evaluations.datasets, staleTime: 60_000, retry: false });
}

export function useEvaluations(filters: EvaluationFilters) {
  return useQuery({
    queryKey: qk.evaluations(filters),
    queryFn: () => api.evaluations.list(filters),
    placeholderData: keepPreviousData,
    retry: false,
    refetchInterval: (query) => evaluationPollInterval(query.state.data?.items.map((r) => r.status) ?? []),
  });
}

export function useEvaluation(id: string | undefined) {
  return useQuery({
    queryKey: qk.evaluation(id ?? ""),
    queryFn: () => api.evaluations.get(id as string),
    enabled: Boolean(id),
    refetchInterval: (query) => evaluationPollInterval([query.state.data?.status]),
  });
}

export function useRunEvaluation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EvaluationRunRequest) => api.evaluations.run(body),
    onSuccess: () => void client.invalidateQueries({ queryKey: qk.evaluationsAll }),
  });
}
