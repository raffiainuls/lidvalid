import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Connection, ConnectionInput, Engine, TestConnectionResult } from "@/lib/types";

export function useConnections() {
  return useQuery({
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
  });
}

export function useEngines() {
  return useQuery({
    queryKey: ["engines"],
    queryFn: () => api.get<{ engines: Engine[] }>("/connections/engines"),
    staleTime: Infinity,
  });
}

export function useCreateConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConnectionInput) => api.post<Connection>("/connections", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
}

export function useUpdateConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ConnectionInput }) =>
      api.put<Connection>(`/connections/${id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
}

export function useDeleteConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete(`/connections/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
}

export function useTestConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<TestConnectionResult>(`/connections/${id}/test`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
}
