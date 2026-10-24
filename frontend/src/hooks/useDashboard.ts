import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import { qk } from "./keys";

export function useDashboard() {
  return useQuery({ queryKey: qk.dashboard, queryFn: api.dashboard });
}
