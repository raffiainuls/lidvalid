import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

export interface ProfileInput {
  display_name: string;
  username: string;
}

export interface PasswordChangeInput {
  current_password: string;
  new_password: string;
  confirm_password: string;
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileInput) => api.put<User>("/profile", body),
    onSuccess: (user) => {
      queryClient.setQueryData(["me"], user);
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: PasswordChangeInput) => api.post<{ ok: boolean }>("/profile/password", body),
  });
}
