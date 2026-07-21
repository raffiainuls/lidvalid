import { useState } from "react";
import { toast } from "sonner";
import { X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useConfigShares,
  useCreateConfigShare,
  useDeleteConfigShare,
  useUpdateConfigShare,
} from "@/hooks/use-config-shares";
import { ApiError } from "@/lib/api";
import type { ConfigSharePermission } from "@/lib/types";

const PERMISSION_OPTIONS: { value: ConfigSharePermission; label: string }[] = [
  { value: "view", label: "Lihat Saja — cuma bisa lihat config, hasil, dan history" },
  { value: "run", label: "Lihat & Jalankan — boleh juga trigger/cancel run" },
  { value: "edit", label: "Edit Penuh — boleh juga ubah pemetaan tabel" },
];

function permissionLabel(p: ConfigSharePermission) {
  return { view: "Lihat Saja", run: "Lihat & Jalankan", edit: "Edit Penuh" }[p];
}

export function ShareConfigDialog({
  open,
  onOpenChange,
  configId,
  configName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  configId: number;
  configName: string;
}) {
  const { data: shares, isLoading } = useConfigShares(configId);
  const createMutation = useCreateConfigShare(configId);
  const updateMutation = useUpdateConfigShare(configId);
  const deleteMutation = useDeleteConfigShare(configId);

  const [username, setUsername] = useState("");
  const [permission, setPermission] = useState<ConfigSharePermission>("view");

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim()) return;
    try {
      await createMutation.mutateAsync({ username: username.trim(), permission });
      toast.success(`Config dibagikan ke ${username.trim()}`);
      setUsername("");
      setPermission("view");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal membagikan config");
    }
  }

  async function handlePermissionChange(shareId: number, next: ConfigSharePermission) {
    try {
      await updateMutation.mutateAsync({ shareId, permission: next });
      toast.success("Level akses diperbarui");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memperbarui akses");
    }
  }

  async function handleRemove(shareId: number) {
    try {
      await deleteMutation.mutateAsync(shareId);
      toast.success("Akses dicabut");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal mencabut akses");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Bagikan Config</DialogTitle>
          <DialogDescription>
            Bagikan akses ke <strong>{configName}</strong> ke user lain -- mereka tidak jadi
            pemilik, cuma dapat akses sesuai level yang dipilih.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          <Label className="text-xs text-muted-foreground">Sudah dibagikan ke</Label>
          {isLoading ? (
            <Skeleton className="h-16" />
          ) : shares && shares.length ? (
            <div className="grid gap-2">
              {shares.map((s) => (
                <div key={s.id} className="flex items-center gap-2 rounded-lg border p-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{s.display_name || s.username}</p>
                    <p className="truncate text-xs text-muted-foreground">{s.username}</p>
                  </div>
                  <Select
                    value={s.permission}
                    onValueChange={(v) => handlePermissionChange(s.id, v as ConfigSharePermission)}
                  >
                    <SelectTrigger className="h-8 w-[9.5rem] text-xs">
                      <SelectValue>{permissionLabel(s.permission)}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {PERMISSION_OPTIONS.map((p) => (
                        <SelectItem key={p.value} value={p.value}>
                          {p.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-8 shrink-0"
                    aria-label={`Cabut akses ${s.username}`}
                    onClick={() => handleRemove(s.id)}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed p-3 text-center text-xs text-muted-foreground">
              Belum dibagikan ke siapa pun.
            </p>
          )}
        </div>

        <form className="grid gap-3 border-t pt-4" onSubmit={handleAdd}>
          <Label className="text-xs text-muted-foreground">Bagikan ke user baru</Label>
          <div className="flex gap-2">
            <Input
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="flex-1"
            />
            <Select value={permission} onValueChange={(v) => setPermission(v as ConfigSharePermission)}>
              <SelectTrigger className="w-[9.5rem] text-xs">
                <SelectValue>{permissionLabel(permission)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {PERMISSION_OPTIONS.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={createMutation.isPending || !username.trim()}>
            {createMutation.isPending ? "Membagikan…" : "Bagikan"}
          </Button>
        </form>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Tutup
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
