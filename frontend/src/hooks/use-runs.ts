import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  DiffFindingRow,
  MissingFindingRow,
  RowlevelResult,
  RunDetail,
  RunTableDrilldown,
} from "@/lib/types";

const ACTIVE_RUN_STATUSES = new Set(["running", "queued"]);

export function useRun(runId: number) {
  return useQuery({
    queryKey: ["runs", runId],
    queryFn: () => api.get<RunDetail>(`/runs/${runId}`),
    enabled: Number.isFinite(runId),
    // Mirrors the old page's 2s fetch() poll loop, but as a partial refetch
    // (React re-renders only what changed) instead of re-fetching+splicing
    // an HTML fragment by hand.
    refetchInterval: (query) => (ACTIVE_RUN_STATUSES.has(query.state.data?.status ?? "") ? 2000 : false),
  });
}

export function useCancelRun(runId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ ok: boolean }>(`/runs/${runId}/cancel`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["runs", runId] }),
  });
}

export function useResumeRun(runId: number) {
  return useMutation({
    mutationFn: (scope: string) => api.post<{ run_id: number }>(`/runs/${runId}/resume`, { scope }),
  });
}

export function useRunTable(runId: number, runTableId: number) {
  return useQuery({
    queryKey: ["runs", runId, "tables", runTableId],
    queryFn: () => api.get<RunTableDrilldown>(`/runs/${runId}/tables/${runTableId}`),
    enabled: Number.isFinite(runId) && Number.isFinite(runTableId),
  });
}

export function useRunTableRowlevel(
  runId: number,
  runTableId: number,
  type: "missing" | "diffs",
  column: string,
  page: number,
) {
  return useQuery({
    queryKey: ["runs", runId, "tables", runTableId, "rowlevel", type, column, page],
    queryFn: () => {
      const params = new URLSearchParams({ type, page: String(page) });
      if (column) params.set("column", column);
      return api.get<RowlevelResult<MissingFindingRow | DiffFindingRow>>(
        `/runs/${runId}/tables/${runTableId}/rowlevel?${params}`,
      );
    },
    enabled: Number.isFinite(runId) && Number.isFinite(runTableId),
    placeholderData: (prev) => prev,
  });
}

export function keysUrl(runId: number, runTableId: number, kind: string, column?: string) {
  const params = new URLSearchParams({ kind });
  if (column) params.set("column", column);
  return `/api/runs/${runId}/tables/${runTableId}/keys?${params}`;
}
