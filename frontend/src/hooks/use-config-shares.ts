import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ConfigShare, ConfigShareCreateInput, ConfigSharePermission } from "@/lib/types";

export function useConfigShares(configId: number) {
  return useQuery({
    queryKey: ["configs", configId, "shares"],
    queryFn: () => api.get<ConfigShare[]>(`/configs/${configId}/shares`),
    enabled: Number.isFinite(configId),
  });
}

export function useCreateConfigShare(configId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfigShareCreateInput) => api.post<ConfigShare>(`/configs/${configId}/shares`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configs", configId, "shares"] });
      queryClient.invalidateQueries({ queryKey: ["configs"] });
    },
  });
}

export function useUpdateConfigShare(configId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shareId, permission }: { shareId: number; permission: ConfigSharePermission }) =>
      api.put<ConfigShare>(`/configs/${configId}/shares/${shareId}`, { permission }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configs", configId, "shares"] });
      queryClient.invalidateQueries({ queryKey: ["configs"] });
    },
  });
}

export function useDeleteConfigShare(configId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (shareId: number) => api.delete<{ ok: boolean }>(`/configs/${configId}/shares/${shareId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configs", configId, "shares"] });
      queryClient.invalidateQueries({ queryKey: ["configs"] });
    },
  });
}
