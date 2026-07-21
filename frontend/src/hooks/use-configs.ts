import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  ConfigCreateInput,
  ConfigDetail,
  ConfigListItem,
  ConfigStatusData,
  ConfigTableRowInput,
  SuggestResult,
} from "@/lib/types";

export function useConfigs() {
  return useQuery({
    queryKey: ["configs"],
    queryFn: () => api.get<ConfigListItem[]>("/configs"),
  });
}

export function useConfig(id: number) {
  return useQuery({
    queryKey: ["configs", id],
    queryFn: () => api.get<ConfigDetail>(`/configs/${id}`),
    enabled: Number.isFinite(id),
  });
}

export function useCreateConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfigCreateInput) => api.post<{ id: number }>("/configs", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["configs"] }),
  });
}

export function useSaveConfigTables(configId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rows: ConfigTableRowInput[]) =>
      api.put<{ tables: ConfigDetail["tables"]; table_columns: ConfigDetail["table_columns"] }>(
        `/configs/${configId}/tables`,
        { rows },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configs", configId] });
      queryClient.invalidateQueries({ queryKey: ["configs"] });
    },
  });
}

export function useSuggestMappings(configId: number) {
  return useMutation({
    mutationFn: (prefix: string) => api.post<SuggestResult>(`/configs/${configId}/suggest`, { prefix }),
  });
}

export function useCopyFromConfig(configId: number) {
  return useMutation({
    mutationFn: (sourceConfigId: number) =>
      api.post<SuggestResult>(`/configs/${configId}/copy-from`, { source_config_id: sourceConfigId }),
  });
}

export function useRunConfig(configId: number) {
  return useMutation({
    mutationFn: (mode: string) => api.post<{ run_id: number }>(`/configs/${configId}/run`, { mode }),
  });
}

const ACTIVE_STATUSES = new Set(["running", "pending", "queued"]);

export function useConfigStatus(configId: number) {
  return useQuery({
    queryKey: ["configs", configId, "status"],
    queryFn: () => api.get<ConfigStatusData>(`/configs/${configId}/status`),
    enabled: Number.isFinite(configId),
    // Mirrors the old page's `setTimeout(reload, 5000)` while anything is
    // still in flight, but as a partial refetch instead of a full reload.
    refetchInterval: (query) => {
      const data = query.state.data;
      const hasActive = data?.rows.some((r) => r.latest && ACTIVE_STATUSES.has(r.latest.status));
      return hasActive ? 5000 : false;
    },
  });
}

export function useRerunTable(configId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sourceTable: string) =>
      api.post<{ run_id: number }>(`/configs/${configId}/rerun-table`, { source_table: sourceTable }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["configs", configId, "status"] }),
  });
}

export function useTableColumns() {
  return useMutation({
    mutationFn: ({ configId, table, side }: { configId: number; table: string; side: "source" | "target" }) =>
      api.get<{ columns: string[]; error?: string }>(
        `/configs/${configId}/table-columns?table=${encodeURIComponent(table)}&side=${side}`,
      ),
  });
}
