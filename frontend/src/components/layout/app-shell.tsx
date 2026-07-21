import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { LayoutDashboard, FolderKanban, Plug, UserRound, Users, LogOut, ShieldCheck } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { useMe } from "@/hooks/use-me";
import { api } from "@/lib/api";
import { toast } from "sonner";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/configs", label: "Configs", icon: FolderKanban },
  { to: "/connections", label: "Connections", icon: Plug },
];

function initials(name: string) {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase())
      .join("") || "?"
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { data: user } = useMe();

  async function handleLogout() {
    try {
      await api.post("/logout");
    } catch {
      // best-effort -- clearing local state still logs the UI out below
    }
    // A hard redirect, not client-side navigate(). Clearing the cached user
    // and navigating within the SPA both notify/rerender ProtectedRoute for
    // the page we're leaving WHILE it's still mounted -- it reacts by
    // scheduling its OWN redirect to /login?next=..., racing the one here
    // (stray query param at best, a flash of the now-401ing page at worst).
    // A full page load sidesteps the race entirely: nothing is left mounted
    // to react to anything, and React Query's whole cache is gone with it.
    window.location.assign("/login");
  }

  return (
    <TooltipProvider>
      <SidebarProvider>
        <Sidebar collapsible="icon">
          <SidebarHeader>
            <div className="flex items-center gap-2 px-2 py-1.5">
              <ShieldCheck className="size-5 text-primary shrink-0" />
              <span className="font-semibold group-data-[collapsible=icon]:hidden">LidValid</span>
            </div>
          </SidebarHeader>
          <SidebarContent>
            <SidebarMenu className="px-2">
              {NAV_ITEMS.map((item) => (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton
                    asChild
                    isActive={location.pathname.startsWith(item.to)}
                    tooltip={item.label}
                  >
                    <Link to={item.to}>
                      <item.icon />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
              {user?.role === "admin" && (
                <SidebarMenuItem>
                  <SidebarMenuButton asChild isActive={location.pathname.startsWith("/users")} tooltip="Users">
                    <Link to="/users">
                      <Users />
                      <span>Users</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={location.pathname.startsWith("/profile")} tooltip="Profil">
                  <Link to="/profile">
                    <UserRound />
                    <span>Profil</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarContent>
          <SidebarFooter>
            <Separator className="mb-2" />
            <div className="flex items-center gap-2 px-2 py-1">
              <Avatar className="size-7 shrink-0">
                <AvatarFallback className="text-xs">
                  {initials(user?.display_name || user?.username || "")}
                </AvatarFallback>
              </Avatar>
              <div className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
                <p className="truncate text-sm font-medium leading-none">
                  {user?.display_name || user?.username}
                </p>
                <p className="truncate text-xs text-muted-foreground capitalize">{user?.role}</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="shrink-0 group-data-[collapsible=icon]:hidden"
                aria-label="Logout"
                onClick={() => {
                  toast.promise(handleLogout(), {
                    loading: "Keluar…",
                    success: "Berhasil keluar",
                    error: "Gagal keluar",
                  });
                }}
              >
                <LogOut className="size-4" />
              </Button>
            </div>
          </SidebarFooter>
        </Sidebar>
        <SidebarInset>
          <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
            <SidebarTrigger />
            <Separator orientation="vertical" className="h-4" />
            <div className="flex-1" />
            <ThemeToggle />
          </header>
          <main className="flex-1 overflow-auto p-6">{children}</main>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}
