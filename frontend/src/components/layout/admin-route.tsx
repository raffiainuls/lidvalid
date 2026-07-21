import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useMe } from "@/hooks/use-me";

export function AdminRoute({ children }: { children: ReactNode }) {
  const { data, isLoading } = useMe();

  if (isLoading) {
    return (
      <div className="flex min-h-svh items-center justify-center">
        <div className="text-sm text-muted-foreground">Memuat…</div>
      </div>
    );
  }
  if (!data || data.role !== "admin") {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}
