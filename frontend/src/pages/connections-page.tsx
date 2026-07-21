import { useState } from "react";
import { toast } from "sonner";
import { Plus } from "lucide-react";
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
import { ConnectionFormDialog } from "@/components/connections/connection-form-dialog";
import { useConnections, useDeleteConnection, useTestConnection } from "@/hooks/use-connections";
import { ApiError } from "@/lib/api";
import type { Connection } from "@/lib/types";

const CONN_STATUS_LABEL: Record<Connection["status"], string> = {
  ok: "OK",
  failed: "Gagal",
  unknown: "Belum diuji",
};

export default function ConnectionsPage() {
  const { data: connections, isLoading } = useConnections();
  const testMutation = useTestConnection();
  const deleteMutation = useDeleteConnection();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Connection | null>(null);
  const [deleting, setDeleting] = useState<Connection | null>(null);

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }
  function openEdit(conn: Connection) {
    setEditing(conn);
    setFormOpen(true);
  }

  async function handleTest(conn: Connection) {
    try {
      const result = await testMutation.mutateAsync(conn.id);
      if (result.ok) {
        toast.success(`Test OK (${result.latency_ms}ms)`);
      } else {
        toast.error(`Test gagal: ${result.error}`);
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal menguji koneksi");
    }
  }

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.id);
      toast.success("Koneksi dihapus");
      setDeleting(null);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal menghapus koneksi");
    }
  }

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Connections</h1>
        <Button onClick={openCreate}>
          <Plus className="size-4" /> Tambah Koneksi
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
                  <TableHead>Engine</TableHead>
                  <TableHead>Host</TableHead>
                  <TableHead>Database</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Aksi</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {connections && connections.length ? (
                  connections.map((conn) => (
                    <TableRow key={conn.id}>
                      <TableCell className="font-medium">{conn.name}</TableCell>
                      <TableCell className="font-mono text-xs">{conn.engine}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {conn.host}
                        {conn.port ? `:${conn.port}` : ""}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{conn.database}</TableCell>
                      <TableCell>
                        <StatusBadge
                          status={conn.status === "ok" ? "pass" : conn.status === "failed" ? "error" : "pending"}
                          label={CONN_STATUS_LABEL[conn.status]}
                        />
                        {conn.last_test_message && (
                          <p className="mt-0.5 truncate text-xs text-muted-foreground">
                            {conn.last_test_message}
                          </p>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1.5">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleTest(conn)}
                            disabled={testMutation.isPending}
                          >
                            Test
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => openEdit(conn)}>
                            Edit
                          </Button>
                          <Button size="sm" variant="destructive" onClick={() => setDeleting(conn)}>
                            Hapus
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-sm text-muted-foreground">
                      Belum ada koneksi.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConnectionFormDialog open={formOpen} onOpenChange={setFormOpen} connection={editing} />

      <AlertDialog open={!!deleting} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Hapus koneksi {deleting?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Tindakan ini tidak bisa dibatalkan. Koneksi yang masih dipakai oleh sebuah config tidak
              akan bisa dihapus.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Batal</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={deleteMutation.isPending}>
              Hapus
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
