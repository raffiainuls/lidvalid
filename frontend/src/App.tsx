import { Routes, Route } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { ProtectedRoute } from "@/components/layout/protected-route";
import { AdminRoute } from "@/components/layout/admin-route";
import HomeRoute from "@/pages/home-route";
import LoginPage from "@/pages/login-page";
import RegisterPage from "@/pages/register-page";
import DashboardPage from "@/pages/dashboard-page";
import ConnectionsPage from "@/pages/connections-page";
import ProfilePage from "@/pages/profile-page";
import ConfigsPage from "@/pages/configs-page";
import ConfigDetailPage from "@/pages/config-detail-page";
import ConfigStatusPage from "@/pages/config-status-page";
import RunDetailPage from "@/pages/run-detail-page";
import TableDrilldownPage from "@/pages/table-drilldown-page";
import UsersPage from "@/pages/users-page";
import { PlaceholderPage } from "@/pages/placeholder-page";

function Protected({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <AppShell>{children}</AppShell>
    </ProtectedRoute>
  );
}

function AdminProtected({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <AdminRoute>
        <AppShell>{children}</AppShell>
      </AdminRoute>
    </ProtectedRoute>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomeRoute />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      <Route path="/dashboard" element={<Protected><DashboardPage /></Protected>} />
      <Route path="/connections" element={<Protected><ConnectionsPage /></Protected>} />
      <Route path="/profile" element={<Protected><ProfilePage /></Protected>} />
      <Route path="/configs" element={<Protected><ConfigsPage /></Protected>} />
      <Route path="/configs/:id" element={<Protected><ConfigDetailPage /></Protected>} />
      <Route path="/configs/:id/status" element={<Protected><ConfigStatusPage /></Protected>} />
      <Route path="/runs/:id" element={<Protected><RunDetailPage /></Protected>} />
      <Route path="/runs/:runId/tables/:runTableId" element={<Protected><TableDrilldownPage /></Protected>} />
      <Route path="/users" element={<AdminProtected><UsersPage /></AdminProtected>} />

      <Route path="*" element={<PlaceholderPage title="404" note="Halaman tidak ditemukan." />} />
    </Routes>
  );
}
