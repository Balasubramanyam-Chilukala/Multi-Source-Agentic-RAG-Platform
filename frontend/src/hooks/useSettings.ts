import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import type { WorkspaceSettingsUpdate } from "@/api/types";
import { qk } from "./keys";

export function useWorkspaceSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: api.settings.get });
}

export function useSystemInfo() {
  return useQuery({ queryKey: qk.system, queryFn: api.settings.system, staleTime: 5 * 60_000 });
}

export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: WorkspaceSettingsUpdate) => api.settings.update(body),
    onSuccess: (data) => client.setQueryData(qk.settings, data),
  });
}
