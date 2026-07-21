import { useState } from "react";
import { toast } from "sonner";
import { Plus, KeyRound, UserX, UserCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { StatusBadge } from "@/components/status-badge";
import { UserFormDialog } from "@/components/users/user-form-dialog";
import { ResetPasswordDialog } from "@/components/users/reset-password-dialog";
import { useMe } from "@/hooks/use-me";
import { useUpdateUser, useUsers } from "@/hooks/use-users";
import { formatDateTime } from "@/lib/format";
import { ApiError } from "@/lib/api";
import type { UserAccount } from "@/lib/types";

const ROLE_LABEL: Record<string, string> = { admin: "Admin", editor: "Editor", viewer: "Viewer" };
const ROLE_STATUS: Record<string, string> = { admin: "pass", editor: "running", viewer: "pending" };

export default function UsersPage() {
  const { data: me } = useMe();
  const { data: users, isLoading } = useUsers();
  const updateMutation = useUpdateUser();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<UserAccount | null>(null);
  const [resetTarget, setResetTarget] = useState<UserAccount | null>(null);
  const [deactivateTarget, setDeactivateTarget] = useState<UserAccount | null>(null);

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }
  function openEdit(u: UserAccount) {
    setEditing(u);
    setFormOpen(true);
  }

  async function handleReactivate(u: UserAccount) {
    try {
      await updateMutation.mutateAsync({
        id: u.id,
        body: { display_name: u.display_name, username: u.username, role: u.role, is_active: true },
      });
      toast.success(`${u.username} diaktifkan kembali`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal mengaktifkan user");
    }
  }

  async function handleDeactivate() {
    if (!deactivateTarget) return;
    try {
      await updateMutation.mutateAsync({
        id: deactivateTarget.id,
        body: {
          display_name: deactivateTarget.display_name,
          username: deactivateTarget.username,
          role: deactivateTarget.role,
          is_active: false,
        },
      });
      toast.success(`${deactivateTarget.username} dinonaktifkan`);
      setDeactivateTarget(null);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal menonaktifkan user");
    }
  }

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <Button onClick={openCreate}>
          <Plus className="size-4" /> Buat User
        </Button>
      </div>

      <Card>
        <CardContent>
          {isLoading ? (
            <div className="grid gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nama</TableHead>
                  <TableHead>Username</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Dibuat</TableHead>
                  <TableHead className="text-right">Aksi</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users && users.length ? (
                  users.map((u) => (
                    <TableRow key={u.id}>
                      <TableCell className="font-medium">
                        {u.display_name || "—"}
                        {u.id === me?.id && (
                          <span className="ml-1.5 text-xs text-muted-foreground">(Anda)</span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{u.username}</TableCell>
                      <TableCell>
                        <StatusBadge status={ROLE_STATUS[u.role]} label={ROLE_LABEL[u.role]} />
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={u.is_active ? "pass" : "pending"} label={u.is_active ? "Aktif" : "Nonaktif"} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{formatDateTime(u.created_at)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1.5">
                          <Button size="sm" variant="outline" onClick={() => openEdit(u)}>
                            Edit
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => setResetTarget(u)}>
                            <KeyRound className="size-3.5" /> Reset Password
                          </Button>
                          {u.id !== me?.id && (
                            u.is_active ? (
                              <Button size="sm" variant="destructive" onClick={() => setDeactivateTarget(u)}>
                                <UserX className="size-3.5" /> Nonaktifkan
                              </Button>
                            ) : (
                              <Button size="sm" variant="outline" onClick={() => handleReactivate(u)}>
                                <UserCheck className="size-3.5" /> Aktifkan
                              </Button>
                            )
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-sm text-muted-foreground">
                      Belum ada user.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <UserFormDialog open={formOpen} onOpenChange={setFormOpen} user={editing} />
      <ResetPasswordDialog open={!!resetTarget} onOpenChange={(v) => !v && setResetTarget(null)} user={resetTarget} />

      <AlertDialog open={!!deactivateTarget} onOpenChange={(open) => !open && setDeactivateTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Nonaktifkan {deactivateTarget?.username}?</AlertDialogTitle>
            <AlertDialogDescription>
              Akun ini langsung tidak bisa login lagi, dan sesi yang sedang aktif langsung terputus.
              Bisa diaktifkan lagi kapan saja.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Batal</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeactivate} disabled={updateMutation.isPending}>
              Nonaktifkan
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
